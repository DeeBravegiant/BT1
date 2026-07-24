### Title
Native Fee (STRK) Permanently Locked in Starknet OmniBridge with No Withdrawal Path - (`starknet/src/omni_bridge.cairo`)

### Summary

The Starknet `OmniBridge.init_transfer` function collects `native_fee` (STRK tokens) directly into the contract itself, but the contract exposes no function to withdraw or redistribute those accumulated fees. Every STRK token paid as a native bridge fee is permanently locked.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the public `init_transfer` function accepts a `native_fee` parameter. When non-zero, it pulls STRK tokens from the caller into the contract's own address:

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
``` [1](#0-0) 

The entire public interface of `OmniBridge` is:

- `log_metadata`, `deploy_token`, `fin_transfer`, `init_transfer`
- `upgrade_token`, `set_pause_flags`, `pause_all`
- `get_token_address`, `is_bridge_token`, `is_transfer_finalised`
- `upgrade` (contract self-upgrade) [2](#0-1) 

None of these functions transfer STRK tokens out of the contract. There is no `withdraw_fee`, `claim_fee`, or treasury-sweep function. Every STRK `native_fee` paid by every user accumulates in the contract with no retrieval path.

The same structural issue exists in the EVM `OmniBridge.sol`: `initTransfer` retains the `nativeFee` portion of `msg.value` inside the contract (computed as `extensionValue = msg.value - nativeFee`), and the contract has no ETH withdrawal function despite having `receive() external payable {}`. [3](#0-2) [4](#0-3) 

### Impact Explanation

Every STRK token paid as `native_fee` on Starknet, and every ETH wei paid as `nativeFee` on EVM, is permanently unclaimable by the protocol. This matches the allowed impact: **Irreversible fund lock — permanently unclaimable protocol value in bridge fee flows.** The loss grows monotonically with bridge usage volume.

### Likelihood Explanation

`init_transfer` is the primary user-facing bridge entry point, called for every cross-chain transfer that includes a native fee. The `native_fee` field is a normal, documented part of the bridge fee model. Any user paying a native fee triggers the lock. No special conditions or attacker action is required — ordinary protocol usage causes the loss.

### Recommendation

Add a privileged `withdraw_fee` (or `sweep_fees`) function callable only by the DAO/admin role that transfers accumulated STRK (Starknet) or ETH (EVM) to a designated treasury address. For example on Starknet:

```cairo
fn withdraw_native_fees(ref self: ContractState, recipient: ContractAddress) {
    self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
    let native_token = self.strk_token_address.read();
    let balance = IERC20Dispatcher { contract_address: native_token }
        .balance_of(get_contract_address());
    if balance > 0 {
        IERC20Dispatcher { contract_address: native_token }
            .transfer(recipient, balance);
    }
}
```

### Proof of Concept

1. User calls `OmniBridge::init_transfer(token, amount, fee, native_fee=1_000_000, recipient, msg)` on Starknet with `native_fee = 1_000_000` STRK units.
2. The contract executes `transfer_from(caller, get_contract_address(), 1_000_000)` — STRK moves into the bridge contract. [1](#0-0) 
3. The `InitTransfer` event is emitted; the relayer processes the transfer normally.
4. The 1,000,000 STRK units sit in the contract. No function in the contract's ABI can move them out. [2](#0-1) 
5. Repeat for every bridge user who pays a native fee. All STRK fees accumulate and are permanently locked.

### Citations

**File:** starknet/src/omni_bridge.cairo (L9-32)
```text
pub trait IOmniBridge<TContractState> {
    fn log_metadata(ref self: TContractState, token: ContractAddress);
    fn deploy_token(ref self: TContractState, signature: Signature, payload: MetadataPayload);
    fn fin_transfer(
        ref self: TContractState, signature: Signature, payload: TransferMessagePayload,
    );
    fn init_transfer(
        ref self: TContractState,
        token_address: ContractAddress,
        amount: u128,
        fee: u128,
        native_fee: u128,
        recipient: ByteArray,
        message: ByteArray,
    );
    fn upgrade_token(
        ref self: TContractState, token_address: ContractAddress, new_class_hash: ClassHash,
    );
    fn set_pause_flags(ref self: TContractState, flags: u8);
    fn pause_all(ref self: TContractState);
    fn get_token_address(self: @TContractState, token_id: ByteArray) -> ContractAddress;
    fn is_bridge_token(self: @TContractState, token_address: ContractAddress) -> bool;
    fn is_transfer_finalised(self: @TContractState, nonce: u64) -> bool;
}
```

**File:** starknet/src/omni_bridge.cairo (L309-314)
```text
            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-413)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
