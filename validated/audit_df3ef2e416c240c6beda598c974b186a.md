### Title
Missing Token Recovery Mechanism Causes Permanent Freezing of WETH Dust — (`contracts/bridges/TACWETHBridge.sol`)

### Summary

`TACWETHBridge` pulls the full user-supplied `amount` of WETH into itself before calling `wethOFT.send()`. The OFT standard allows `amountSentLD` (tokens actually debited from the bridge) to be **less than** `amountLD` due to shared-decimal rounding. The difference is left in the bridge permanently. No function in the contract — not even `DEFAULT_ADMIN_ROLE` — can move ERC-20 tokens out. Every sibling bridge in the codebase has a recovery path; `TACWETHBridge` does not.

---

### Finding Description

`bridgeTokenToL1` executes in two steps:

1. Pull `amount` WETH from the caller into the bridge: [1](#0-0) 

2. Call `wethOFT.send()` with `amountLD: amount`: [2](#0-1) 

The `OFTReceipt` struct documents that `amountSentLD` is the amount **actually debited** from the sender (the bridge), which can be strictly less than `amountLD`: [3](#0-2) 

In the standard LayerZero OFT implementation, `_debitView` rounds `amountLD` down to the nearest shared-decimal unit (`_removeDust`). For a WETH OFT with 18 local decimals and 6 shared decimals, up to `10^12 − 1` wei per call is left unburned in the bridge. The contract never checks `oftReceipt.amountSentLD` against `amount`, so the dust silently accumulates.

The complete set of external/public functions in `TACWETHBridge` is:

| Function | Can move WETH out? |
|---|---|
| `setSlippageTolerance` | No — parameter setter only |
| `bridgeTokenToL1` | No — only sends, never refunds |
| `getNativeFee` | No — view |
| `getMinAmount` | No — view |
| `getReceiver` | No — pure | [4](#0-3) 

The protocol already ships a `Recoverable` abstract contract and uses it in sibling bridges: [5](#0-4) 

`SonicChainNativeTokenBridge` has `recoverTokens`: [6](#0-5) 

`SonicBridgeReceiver` has `emergencyRecover`: [7](#0-6) 

`TACWETHBridge` inherits only `AccessControl` and `ReentrancyGuard` and adds no equivalent: [8](#0-7) 

---

### Impact Explanation

Any WETH that enters the bridge without being fully consumed by `wethOFT.send()` is permanently frozen. The concrete, in-protocol path is OFT shared-decimal rounding: every normal call to `bridgeTokenToL1` can leave up to `10^12 − 1` wei of WETH in the contract. Additionally, any WETH sent directly to the bridge address (e.g., by a user or an automated system that mistakenly transfers before calling the bridge) is also permanently unrecoverable. There is no upgrade path, no admin escape hatch, and no `receive()` fallback that could be exploited to drain it.

---

### Likelihood Explanation

The decimal-rounding path triggers on **every** call to `bridgeTokenToL1` when the OFT uses shared decimals smaller than 18 (the standard LayerZero OFT configuration for WETH). No special attacker action is required; normal user bridging activity is sufficient. The direct-transfer path requires only a single mistaken ERC-20 transfer to the bridge address, which is a realistic operational error.

---

### Recommendation

Inherit from the existing `Recoverable` utility contract, or add an explicit `recoverTokens` function gated on `DEFAULT_ADMIN_ROLE`, consistent with the pattern already used in `SonicChainNativeTokenBridge` and `SonicBridgeReceiver`. Optionally, after calling `wethOFT.send()`, compare `oftReceipt.amountSentLD` with `amount` and immediately refund any dust to `msg.sender` rather than leaving it in the bridge.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Minimal local test — no mainnet required

contract MockWETHOFT {
    mapping(address => uint256) public balanceOf;

    function transfer(address to, uint256 amt) external returns (bool) {
        balanceOf[msg.sender] -= amt;
        balanceOf[to] += amt;
        return true;
    }
    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
        return true;
    }

    // OFT send: debits only rounded-down amount (simulates shared-decimal dust)
    function send(SendParam calldata p, MessagingFee calldata, address)
        external payable
        returns (MessagingReceipt memory, OFTReceipt memory)
    {
        uint256 dust = p.amountLD % 1e12;          // shared-decimal rounding
        uint256 debited = p.amountLD - dust;        // only this is burned
        balanceOf[msg.sender] -= debited;           // bridge loses only `debited`
        return (
            MessagingReceipt(bytes32(0), 0, MessagingFee(msg.value, 0)),
            OFTReceipt(debited, debited)
        );
    }
    function quoteSend(SendParam calldata, bool) external pure returns (MessagingFee memory) {
        return MessagingFee(0.001 ether, 0);
    }
}

// Test:
// 1. Deploy MockWETHOFT and TACWETHBridge(admin, mockOFT, dstId, 0)
// 2. Mint 1e18 + 500 MockWETHOFT to user; approve bridge
// 3. user calls bridgeTokenToL1{value: 0.001 ether}(recipient, 1e18 + 500)
// 4. Bridge pulls 1e18+500; OFT debits 1e18 (rounds off 500 wei dust)
// 5. Assert MockWETHOFT.balanceOf(bridge) == 500  ← permanently stuck
// 6. Enumerate all bridge external functions → none can move the 500 wei out
// 7. Assert bridge has no recoverTokens / emergencyRecover / sweep → confirmed frozen
```

### Citations

**File:** contracts/bridges/TACWETHBridge.sol (L16-16)
```text
contract TACWETHBridge is IL2TokenBridge, AccessControl, ReentrancyGuard {
```

**File:** contracts/bridges/TACWETHBridge.sol (L86-192)
```text
    function setSlippageTolerance(uint256 newSlippageTolerance) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newSlippageTolerance > BASIS_POINTS_DIVISOR) {
            revert InvalidSlippageTolerance();
        }

        slippageTolerance = newSlippageTolerance;
        emit SlippageToleranceUpdated(newSlippageTolerance);
    }

    /**
     * @notice Bridges WETH from TAC to L1
     * @param recipient The address of the recipient on L1
     * @param amount The amount of wstETH to bridge
     */
    function bridgeTokenToL1(address recipient, uint256 amount) external payable nonReentrant {
        UtilLib.checkNonZeroAddress(recipient);

        if (amount == 0) {
            revert ZeroAmount();
        }

        // Calculate the native fee for bridging
        uint256 nativeFee = getNativeFee(amount, recipient);

        // Check if the msg.value is equal to the native fee for bridging
        if (msg.value != nativeFee) {
            revert InvalidNativeFee();
        }

        // Transfer the tokens to this contract
        IERC20(address(wethOFT)).safeTransferFrom(msg.sender, address(this), amount);

        // Bridge WETH to the L1 recipient
        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(recipient),
            amountLD: amount,
            minAmountLD: getMinAmount(amount),
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        (, OFTReceipt memory oftReceipt) = wethOFT.send{ value: nativeFee }(sendParam, fee, msg.sender);

        emit BridgedWETHToL1(dstLzChainId, recipient, oftReceipt.amountSentLD, oftReceipt.amountReceivedLD);
    }

    /**
     * @dev Quote the native fee for sending WETH to L1
     * @param amount The amount of WETH to send
     * @param receiver The address of the receiver on L1
     * @return The fee to be paid in native currency
     */
    function getNativeFee(uint256 amount, address receiver) public view returns (uint256) {
        UtilLib.checkNonZeroAddress(receiver);

        if (amount == 0) {
            revert ZeroAmount();
        }

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(receiver),
            amountLD: amount,
            minAmountLD: getMinAmount(amount),
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = wethOFT.quoteSend(sendParam, false);

        return fee.nativeFee;
    }

    /**
     * @dev Get the minimum amount after slippage
     * @param amount The amount
     * @return The minimum amount after applying slippage
     */
    function getMinAmount(uint256 amount) public view returns (uint256) {
        if (amount == 0) {
            revert ZeroAmount();
        }

        uint256 minAmount = amount * (BASIS_POINTS_DIVISOR - slippageTolerance) / BASIS_POINTS_DIVISOR;

        if (minAmount == 0) {
            revert InvalidMinAmount();
        }

        return minAmount;
    }

    /**
     * @dev Get the receiver address in the bytes32 format
     * @param receiver The address of the receiver on L1
     * @return The receiver address in the bytes32 format
     */
    function getReceiver(address receiver) public pure returns (bytes32) {
        UtilLib.checkNonZeroAddress(receiver);
        return bytes32(uint256(uint160(receiver)));
    }
}
```

**File:** contracts/external/layerzero/interfaces/IOFT.sol (L37-40)
```text
struct OFTReceipt {
    uint256 amountSentLD; // Amount of tokens ACTUALLY debited from the sender in local decimals
    uint256 amountReceivedLD; // Amount of tokens to be received on the remote side
}
```

**File:** contracts/utils/Recoverable.sol (L41-57)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert ZeroAmount();
        if (IERC20(tokenAddress).balanceOf(address(this)) < amount) revert InsufficientBalance();

        IERC20(tokenAddress).safeTransfer(recipient, amount);

        emit TokensRecovered(tokenAddress, recipient, amount);
    }
```

**File:** contracts/bridges/SonicChainNativeTokenBridge.sol (L160-173)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert InvalidAmount();

        IERC20(tokenAddress).safeTransfer(recipient, amount);
    }
```

**File:** contracts/bridges/SonicBridgeReceiver.sol (L164-172)
```text
    function emergencyRecover(address token, address recipient, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(recipient);

        uint256 balance = IERC20(token).balanceOf(address(this));
        uint256 recoverAmount = amount == 0 ? balance : amount;
        if (recoverAmount > balance) revert InsufficientBalance();

        IERC20(token).safeTransfer(recipient, recoverAmount);
    }
```
