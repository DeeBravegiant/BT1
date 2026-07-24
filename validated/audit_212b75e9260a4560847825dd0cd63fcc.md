### Title
`nativeFee` ETH Permanently Locked in Contract With No Withdrawal Path - (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
Every call to `initTransfer` or `initTransfer1155` that includes a non-zero `nativeFee` leaves that ETH permanently stranded inside `OmniBridge`. The contract retains the `nativeFee` portion of `msg.value` but has no mechanism to forward it to a fee recipient or allow it to be recovered. There is no `withdraw`, `rescue`, or `sweep` function anywhere in the contract.

### Finding Description
In `OmniBridge.initTransfer`, `extensionValue` is computed by subtracting both `amount` and `nativeFee` from `msg.value` for native-ETH transfers, or only `nativeFee` for ERC20 transfers:

```
extensionValue = msg.value - amount - nativeFee   // ETH path
extensionValue = msg.value - nativeFee             // ERC20 path
``` [1](#0-0) 

Only `extensionValue` is passed down to `initTransferExtension`. In `OmniBridgeWormhole`, that override forwards only `value` (i.e., `extensionValue`) to Wormhole's `publishMessage`: [2](#0-1) 

The `nativeFee` delta — `msg.value - extensionValue - amount` — is never forwarded anywhere. It accumulates silently in the contract's ETH balance. The same pattern applies to `initTransfer1155`: [3](#0-2) 

A search across all EVM production files confirms there is no `withdraw`, `rescue`, `sweep`, `reclaim`, or `refund` function anywhere in scope. [4](#0-3) 

### Impact Explanation
`nativeFee` is a first-class protocol parameter emitted in the `InitTransfer` event and is clearly intended to compensate relayers or fee recipients on the destination chain. Because it is never routed to any recipient and the contract provides no administrative withdrawal path, every wei of `nativeFee` paid by every user is permanently unclaimable. This is a fee-routing divergence that sends value to the wrong party (the contract itself) and constitutes an irreversible fund lock in the fee flow — matching the High allowed-impact category.

### Likelihood Explanation
Any unprivileged user who calls `initTransfer` or `initTransfer1155` with `nativeFee > 0` triggers the lock. No special role, key, or external condition is required. The parameter is user-controlled and publicly documented in the ABI. Likelihood is **Medium-High**: the path is reachable by any caller, and the loss scales linearly with usage volume.

### Recommendation
Add a privileged withdrawal function (e.g., restricted to `DEFAULT_ADMIN_ROLE`) that allows the contract owner to sweep accumulated native ETH:

```solidity
function withdrawNative(address payable to, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool ok, ) = to.call{value: amount}("");
    if (!ok) revert FailedToSendEther();
}
```

Alternatively, refund excess ETH to `msg.sender` at the end of `initTransfer` after all deductions, so no ETH beyond `amount` (for native transfers) and the Wormhole fee is ever retained.

### Proof of Concept
1. Deploy `OmniBridgeWormhole` with a mock Wormhole whose `messageFee()` returns `0.001 ether`.
2. Call `initTransfer(someERC20, 100e18, 0, 0.05 ether, "near:alice.near", "")` with `msg.value = 0.051 ether` (0.001 Wormhole fee + 0.05 nativeFee).
3. Observe: Wormhole receives `0.001 ether` (`extensionValue`). The contract's ETH balance increases by `0.05 ether`.
4. Confirm: no function exists to retrieve the `0.05 ether`. It is permanently locked. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

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

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L466-466)
```text
        uint256 extensionValue = msg.value - nativeFee;
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```
