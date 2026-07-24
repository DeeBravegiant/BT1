### Title
`OmniBridge` Permanently Locks `nativeFee` ETH and Any ETH Sent via `receive()` — No Withdrawal Path Exists - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge` collects `nativeFee` ETH on every `initTransfer` call and exposes a bare `receive()` fallback, but contains no function to withdraw accumulated ETH. All ETH that enters the contract through these two paths is permanently locked.

### Finding Description
`OmniBridge.initTransfer` is `payable` and requires callers to include `nativeFee` in `msg.value`. For ERC-20 transfers the accounting is:

```
extensionValue = msg.value - nativeFee;   // line 393
```

Only `extensionValue` is forwarded onward (to Wormhole via `_wormhole.publishMessage{value: value}` in `OmniBridgeWormhole.initTransferExtension`). The `nativeFee` portion is silently retained by the contract with no record and no release path. [1](#0-0) [2](#0-1) 

The only ETH egress in the entire contract is the `finTransfer` branch that sends `payload.amount` to a recipient when `payload.tokenAddress == address(0)` — i.e., native-ETH redemptions. That path is gated by a valid MPC signature and is completely unrelated to accumulated `nativeFee` balances. [3](#0-2) 

Compounding this, the contract also exposes a bare `receive()` fallback with no logic, so any ETH sent directly to the contract address is equally unrecoverable. [4](#0-3) 

There is no `withdrawFees`, `rescueETH`, or any admin function that moves ETH out of the contract. [5](#0-4) 

### Impact Explanation
Every successful `initTransfer` call that includes a non-zero `nativeFee` permanently destroys that ETH — it accumulates in the contract balance and can never be claimed by the protocol, a fee recipient, or the user. Over the lifetime of the bridge this constitutes an ever-growing, irreversible loss of protocol fee value. This matches the allowed critical impact: *"permanently unclaimable user or protocol value in bridge… fee… flows."*

### Likelihood Explanation
`nativeFee` is a first-class parameter of the public `initTransfer` interface and is expected to be non-zero in normal operation (it covers the Wormhole message fee). Every ordinary bridge user triggers this lock on every transfer. No special conditions or attacker action are required; the loss is automatic and cumulative.

### Recommendation
1. Track accumulated `nativeFee` in a storage variable and add an admin `withdrawFees(address payable recipient, uint256 amount)` function protected by `DEFAULT_ADMIN_ROLE`.
2. Remove the bare `receive()` fallback, or replace it with a revert, since no protocol flow legitimately sends ETH to the contract outside of `initTransfer` and `finTransfer`.

### Proof of Concept
1. User calls `initTransfer(tokenERC20, 1e18, 0, 0.001 ether, "near:recipient", "")` with `msg.value = 0.001 ether`.
2. `extensionValue = 0.001 ether - 0.001 ether = 0`. Wormhole receives `0`.
3. `0.001 ether` sits in `OmniBridge`'s balance.
4. No function exists to move it out. Repeat for every bridge user — fees accumulate and are permanently locked. [6](#0-5)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-596)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }

    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }

    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }

    receive() external payable {}

    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }

    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L143-144)
```text
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
```
