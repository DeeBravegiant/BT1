### Title
Fee Baseline Uses Last Stored Price Instead of Peak Price, Charging Fees on Slashing Recovery — (`contracts/LRTOracle.sol`)

---

### Summary

`_updateRsETHPrice()` computes the protocol fee baseline as `previousTVL = rsethSupply * rsETHPrice` (the last stored price), not `rsethSupply * highestRsethPrice`. When the rsETH price drops within the `pricePercentageLimit` (so no pause is triggered and `rsETHPrice` is updated downward), then later recovers above the previous peak, the fee is charged on the full delta from the depressed price — including the recovery of previously-slashed value — rather than only on genuine new yield above the prior peak.

---

### Finding Description

In `LRTOracle._updateRsETHPrice()`, the fee calculation baseline is:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);   // line 234
```

`rsETHPrice` is the **last stored price**, updated at line 313 on every non-pausing call. `highestRsethPrice` is tracked separately and updated only upward (line 294–296), but it is **never used** in the fee calculation.

The downside-pause guard at lines 270–281 only fires when the price drop exceeds `pricePercentageLimit`. When it fires, the function returns early (line 281) **without** updating `rsETHPrice`, so the baseline is preserved. But when the drop is within the limit (or `pricePercentageLimit == 0`), execution continues to line 313 and `rsETHPrice` is written to the depressed value.

On the subsequent recovery call:

- `previousTVL` = `rsethSupply × depressedPrice`
- `totalETHInProtocol` = TVL at recovered price
- `rewardAmount` = `rsethSupply × (recoveredPrice − depressedPrice)`

This `rewardAmount` includes both the recovery of slashed value **and** genuine new yield above the old peak. The fee is therefore inflated by the recovery portion. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Excess rsETH is minted to the treasury (lines 304–307). Because rsETH is a share token, minting extra shares to the treasury dilutes every existing holder's claim on the underlying ETH. The dilution equals the fee taken on the recovery portion, which is a direct, quantifiable transfer of value from rsETH holders to the treasury — **theft of unclaimed yield**. [4](#0-3) 

---

### Likelihood Explanation

- `protocolFeeInBPS > 0` is the normal operational state (manager-configurable, capped at 1500 BPS).
- EigenLayer slashing or LST de-pegging events can cause small, within-limit price drops.
- `pricePercentageLimit` is set by admin; if set to a value larger than the drop, no pause fires and `rsETHPrice` is written down.
- `updateRSETHPrice()` is a public, permissionless function — anyone can trigger the fee mint once the price has recovered.
- No attacker-controlled input is required; the condition arises from normal market events. [5](#0-4) [6](#0-5) 

---

### Recommendation

Replace the fee baseline with the maximum of the last stored price and the all-time-high price, so fees are only charged on genuine new yield above the previous peak:

```solidity
// Use the higher of the last stored price and the all-time high as the fee baseline
uint256 feeBaselinePrice = rsETHPrice > highestRsethPrice ? rsETHPrice : highestRsethPrice;
uint256 previousTVL = rsethSupply.mulWad(feeBaselinePrice);
```

This ensures that when the price recovers from a slashing event, `previousTVL` is anchored to the peak TVL, and `rewardAmount` only captures the portion of growth that is genuinely new yield. [7](#0-6) 

---

### Proof of Concept

State-based test (no external dependencies):

```
Setup:
  rsethSupply = 1000e18
  rsETHPrice  = 1.1e18   (highestRsethPrice = 1.1e18)
  protocolFeeInBPS = 1000  (10%)
  pricePercentageLimit = 0.20e18  (20% — drop of 18% is within limit)

Step 1 — price drops to 0.9e18 (within 20% limit, no pause):
  totalETHInProtocol = 900e18
  previousTVL        = 1000 × 1.1 = 1100e18
  totalETHInProtocol < previousTVL → no fee
  rsETHPrice updated to 0.9e18
  highestRsethPrice  stays at 1.1e18

Step 2 — price recovers to 1.2e18:
  totalETHInProtocol = 1200e18
  previousTVL        = 1000 × 0.9 = 900e18   ← depressed baseline
  rewardAmount       = 1200 - 900 = 300e18
  protocolFeeInETH   = 300e18 × 10% = 30e18

  Correct fee (only yield above peak):
    rewardAmount_correct = 1200 - 1100 = 100e18
    protocolFeeInETH_correct = 100e18 × 10% = 10e18

  Excess fee minted: 30e18 - 10e18 = 20e18 ETH-equivalent in rsETH
  → treasury receives 3× the legitimate fee, stealing yield from holders
```

Assert: `rsethAmountToMintAsProtocolFee` equals `10e18 / newRsETHPrice`, not `30e18 / newRsETHPrice`. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-247)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L293-296)
```text
        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L299-308)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
```

**File:** contracts/LRTConfig.sol (L196-199)
```text
    function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
        if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
        protocolFeeInBPS = _protocolFeeInBPS;
        emit UpdateFee(_protocolFeeInBPS);
```
