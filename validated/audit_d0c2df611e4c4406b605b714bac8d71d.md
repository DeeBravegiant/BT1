### Title
Unprotected Payable Receive Functions Allow ETH to Be Permanently Lost Without Minting rsETH - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool` exposes three `external payable` functions — `receiveFromRewardReceiver()`, `receiveFromLRTConverter()`, and `receiveFromNodeDelegator()` — with no access control and no body. Any ETH sent to these functions by an unprivileged caller is silently absorbed into the deposit pool's ETH balance without minting rsETH for the caller, permanently destroying the caller's funds.

### Finding Description
The three functions at lines 61–67 of `LRTDepositPool.sol` are declared `external payable` with empty bodies and no caller restriction:

```solidity
/// @dev receive from RewardReceiver
function receiveFromRewardReceiver() external payable { }

/// @dev receive from LRTConverter
function receiveFromLRTConverter() external payable { }

/// @dev receive from NodeDelegator
function receiveFromNodeDelegator() external payable { }
``` [1](#0-0) 

These functions are intended to be called only by the protocol's internal contracts (`FeeReceiver`/`RewardReceiver`, `LRTConverter`, and `NodeDelegator`). However, because there is no `onlyRole`, `require(msg.sender == ...)`, or any other guard, any external account or contract can call them and attach ETH. The ETH is accepted by the contract and silently added to the pool's balance.

The legitimate deposit path for users is `depositETH()`, which uses `msg.value` to compute and mint rsETH:

```solidity
uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
``` [2](#0-1) 

A caller who mistakenly invokes `receiveFromRewardReceiver()` (or either of the other two) with ETH bypasses the minting logic entirely. The ETH is absorbed into the pool's TVL, incrementally inflating the rsETH/ETH rate and benefiting all existing rsETH holders, while the caller receives zero rsETH.

### Impact Explanation
The caller's ETH is permanently lost: it enters the pool's balance, is counted in TVL, and is eventually forwarded to node delegators for staking. There is no refund path and no admin recovery function visible in `LRTDepositPool`. The caller receives no rsETH and has no recourse. This constitutes permanent freezing (and effective theft) of user funds.

**Impact: Critical — Permanent freezing / direct loss of user funds.**

### Likelihood Explanation
The likelihood is low-to-medium. The scenario requires a caller to invoke one of the three named functions instead of `depositETH()`. This can occur via:
- A wallet or dApp that enumerates and presents all payable functions.
- A smart contract integration that calls the wrong selector.
- A user copy-pasting a function name from documentation or a block explorer.

The functions are publicly visible in the ABI and carry no warning that ETH sent to them is non-refundable.

### Recommendation
Add access control to each of the three receive functions so only the designated protocol contracts can call them:

```solidity
function receiveFromRewardReceiver() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_REWARD_RECEIVER)) revert CallerNotRewardReceiver();
}
function receiveFromLRTConverter() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_CONVERTER)) revert CallerNotLRTConverter();
}
function receiveFromNodeDelegator() external payable {
    if (isNodeDelegator[msg.sender] != 1) revert CallerNotNodeDelegator();
}
```

Alternatively, following the short-term recommendation from the reference report: revert if `msg.value > 0` and the caller is not the expected protocol contract.

### Proof of Concept
1. Alice holds 1 ETH and intends to deposit via `depositETH()`.
2. Alice (or her wallet) accidentally calls `receiveFromRewardReceiver{value: 1 ether}()` on `LRTDepositPool`.
3. The call succeeds. `LRTDepositPool.balance` increases by 1 ETH.
4. No rsETH is minted for Alice. `_beforeDeposit` and `_mintRsETH` are never called.
5. The 1 ETH is counted in TVL, marginally increasing the rsETH/ETH rate, benefiting all existing rsETH holders.
6. Alice has permanently lost 1 ETH with no recovery path. [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L60-67)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-90)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);
```
