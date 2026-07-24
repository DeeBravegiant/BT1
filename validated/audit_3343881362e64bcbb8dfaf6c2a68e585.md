### Title
`nativeFee` ETH Permanently Locked in `OmniBridge` — No Payout or Recovery Path - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer` and `initTransfer1155` accept `msg.value` that includes a `nativeFee` component, which is retained by the contract. No code path in the contract ever forwards this ETH to a relayer or allows it to be recovered. Every call with `nativeFee > 0` permanently locks ETH in the contract.

### Finding Description
In `initTransfer`, the contract computes `extensionValue = msg.value - nativeFee` (for ERC-20 tokens) or `extensionValue = msg.value - amount - nativeFee` (for native ETH). The `nativeFee` portion is silently retained by the contract. [1](#0-0) 

The base `initTransferExtension` only validates that `extensionValue == 0`; it does nothing with `nativeFee`: [2](#0-1) 

The same pattern applies to `initTransfer1155`: [3](#0-2) 

`finTransfer` only sends `payload.amount` to the recipient; it has no mechanism to pay out accumulated `nativeFee` ETH to any relayer: [4](#0-3) 

The contract has no `withdrawFee`, `claimNativeFee`, or any admin ETH-withdrawal function. The only admin functions are access-control, pause, token-management, and UUPS upgrade operations. The `receive()` fallback accepts ETH but there is no corresponding send path: [5](#0-4) 

### Impact Explanation
Every `initTransfer` or `initTransfer1155` call with `nativeFee > 0` permanently locks ETH in the contract. The relayer that processes the transfer on the NEAR side never receives the EVM-side native fee it was promised. This is an irreversible fund lock of fee value in the bridge contract, matching the Critical impact class.

### Likelihood Explanation
`initTransfer` is a public, permissionless function callable by any user. The `nativeFee` parameter is a standard part of the bridge API documented for users. Any user who follows the documented flow and sets `nativeFee > 0` to incentivize relayers will have that ETH permanently locked. This is a routine, expected usage pattern.

### Recommendation
Track accumulated `nativeFee` per transfer nonce (or in aggregate) and add a payout mechanism. Either:
1. Forward `nativeFee` ETH directly to a configurable `feeRecipient` address at `initTransfer` time, or
2. Add a `claimNativeFee(uint64 originNonce, address payable relayer)` function callable by the relayer after proof of delivery, mirroring the NEAR-side `claim_fee` flow.

The NEAR-side analog correctly handles this: `send_fee_internal` transfers the `native_fee` yoctoNEAR to the relayer via `Promise::new(fee_recipient).transfer(...)` when `claim_fee` is called. [6](#0-5) 

### Proof of Concept
1. User calls `initTransfer(tokenAddress, 1000, 10, 5e16, "near:recipient.near", "")` with `msg.value = 5e16` (0.05 ETH as `nativeFee`).
2. Contract computes `extensionValue = 5e16 - 5e16 = 0`, passes validation.
3. ERC-20 tokens are transferred to `address(this)` (for non-bridge tokens) or burned.
4. `InitTransfer` event is emitted with `nativeFee = 5e16`.
5. The 0.05 ETH sits in the contract balance indefinitely.
6. No function exists to send it to the relayer or return it to the user.
7. Repeat for every bridging user who sets `nativeFee > 0`; ETH accumulates and is permanently unclaimable.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-322)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L466-478)
```text
        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L492-506)
```text
    function initTransferExtension(
        address /*sender*/,
        address /*tokenAddress*/,
        uint64 /*originNonce*/,
        uint128 /*amount*/,
        uint128 /*fee*/,
        uint128 /*nativeFee*/,
        string calldata /*recipient*/,
        string calldata /*message*/,
        uint256 value
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** near/omni-bridge/src/lib.rs (L2669-2672)
```rust
            } else if origin_chain == ChainKind::Near {
                Promise::new(fee_recipient.clone())
                    .transfer(NearToken::from_yoctonear(transfer_message.fee.native_fee.0))
                    .detach();
```
