### Title
`setProtocolFeeBps()` Does Not Settle Accumulated Rewards Before Changing Fee Rate, Enabling Frontrun to Steal Protocol Yield - (File: contracts/LRTConfig.sol)

### Summary

`LRTConfig.setProtocolFeeBps()` updates `protocolFeeInBPS` without first triggering `LRTOracle.updateRSETHPrice()`. Because `_updateRsETHPrice()` computes the protocol fee on all rewards accumulated since the last price update using the **current** `protocolFeeInBPS` at call time, a fee decrease retroactively applies to already-earned rewards. An attacker can frontrun the fee-decrease transaction to deposit a large amount of ETH/LST, acquire rsETH at the stale price, and then capture a disproportionate share of the extra yield that flows to rsETH holders when the fee is reduced.

### Finding Description

`LRTOracle._updateRsETHPrice()` computes the protocol fee on accumulated rewards as:

```solidity
uint256 rewardAmount = totalETHInProtocol - previousTVL;
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
```

where `previousTVL = rsethSupply * rsETHPrice` (the stored price from the last update). The `rewardAmount` represents **all rewards earned since the last `updateRSETHPrice()` call**, which may span hours or days. The fee applied to this entire accumulated period is whatever `protocolFeeInBPS` is at the moment `updateRSETHPrice()` is called — not the rate that was in effect when the rewards were earned.

`setProtocolFeeBps()` in `LRTConfig` simply overwrites the fee without settling the current state:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

No call to `updateRSETHPrice()` is made before the change. This means rewards that accrued under the old fee regime are retroactively subject to the new (lower) fee when the next price update occurs.

### Impact Explanation

When `protocolFeeInBPS` is decreased, the protocol treasury receives less fee on all rewards accumulated since the last price update. The difference flows to rsETH holders as a higher rsETH price. An attacker who deposits just before the fee decrease captures a larger share of this extra yield proportional to their newly acquired rsETH. The protocol loses fee revenue it was entitled to, and the attacker profits at the expense of the treasury. This constitutes theft of unclaimed yield.

### Likelihood Explanation

The MANAGER role is an operational role (likely a multisig) that may adjust `protocolFeeInBPS` as part of normal protocol governance. The attack requires: (1) a pending fee-decrease transaction, (2) accumulated rewards since the last price update (which is routine — `updateRSETHPrice()` is not called on every block), and (3) the attacker to frontrun the fee change with a deposit. All three conditions are realistic on Ethereum mainnet. `updateRSETHPrice()` is a public function callable by anyone, so the attacker can also trigger the price update themselves immediately after the fee change lands.

### Recommendation

Call `updateRSETHPrice()` (or an equivalent internal settlement) inside `setProtocolFeeBps()` before changing the fee, so that all rewards accumulated under the old rate are settled at the old rate:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    // Settle accumulated rewards at the current fee rate before changing it
    ILRTOracle(getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

### Proof of Concept

1. Protocol has `protocolFeeInBPS = 1000` (10%). Rewards of 100 ETH have accumulated since the last `updateRSETHPrice()` call. At 10%, the treasury should receive 10 ETH worth of rsETH fee.
2. MANAGER submits a transaction to call `setProtocolFeeBps(0)` (reducing fee to 0%).
3. Attacker observes the pending transaction in the mempool and frontruns it with a large `depositETH()` call, acquiring rsETH at the current stale `rsETHPrice`.
4. MANAGER's transaction executes: `protocolFeeInBPS` is now 0.
5. Anyone calls `updateRSETHPrice()`. The fee on the 100 ETH of accumulated rewards is now `100 * 0 / 10_000 = 0`. All 100 ETH flows to rsETH holders as a price increase.
6. The attacker's rsETH is now worth more than they paid. They withdraw/sell for a profit proportional to their share of the rsETH supply.
7. The protocol treasury receives 0 instead of the 10 ETH it was entitled to.

**Root cause:** [1](#0-0) 

**Fee applied retroactively to all accumulated rewards at call time:** [2](#0-1) 

**`previousTVL` anchors the reward window to the last stored price:** [3](#0-2) 

**Public entry point for price update (callable by attacker):** [4](#0-3) 

**Public deposit entry point (attacker's frontrun vector):** [5](#0-4)

### Citations

**File:** contracts/LRTConfig.sol (L196-200)
```text
    function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
        if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
        protocolFeeInBPS = _protocolFeeInBPS;
        emit UpdateFee(_protocolFeeInBPS);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-234)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
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

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```
