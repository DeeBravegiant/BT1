### Title
Increasing `protocolFeeInBPS` Without First Settling Accrued Yield Retroactively Steals rsETH Holder Yield - (`contracts/LRTConfig.sol` / `contracts/LRTOracle.sol`)

---

### Summary

`LRTConfig.setProtocolFeeBps()` changes the protocol fee rate without first calling `LRTOracle.updateRSETHPrice()`. Because `_updateRsETHPrice()` computes the fee against **all yield accumulated since the last price update**, a fee increase retroactively applies the higher rate to yield that accrued under the old rate, stealing unclaimed yield from rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` measures yield as the difference between current TVL and the TVL implied by the last stored `rsETHPrice`:

```solidity
// LRTOracle.sol#L234-L246
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);          // TVL at last update
...
uint256 rewardAmount = totalETHInProtocol - previousTVL;       // all yield since last update
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000; // current fee applied
```

The fee is read **at call time** from `lrtConfig.protocolFeeInBPS()` and applied to the entire `rewardAmount` window — which may span days of yield accrual.

`LRTConfig.setProtocolFeeBps()` updates this rate with no prior settlement:

```solidity
// LRTConfig.sol#L196-L200
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

There is no call to `updateRSETHPrice()` before the state change. The next invocation of `updateRSETHPrice()` (a public function) will apply the new, higher fee to the entire unsettled yield window, including yield that accrued while the old, lower fee was in effect.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

rsETH holders earn yield continuously as EigenLayer restaking rewards increase the protocol's TVL. That yield is only crystallised into the rsETH price when `updateRSETHPrice()` is called. Between calls, the yield is "pending" — it belongs to holders but has not yet been reflected in the price.

When `protocolFeeInBPS` is raised from `X` to `Y` (Y > X) without first settling, the next `updateRSETHPrice()` call mints extra rsETH to the treasury equal to:

```
extra_fee = rewardAmount * (Y - X) / 10_000
```

where `rewardAmount` covers the entire unsettled window. This extra rsETH dilutes all existing holders, transferring value that rightfully belonged to them to the protocol treasury. The magnitude scales with both the fee delta and the length of the unsettled window (which can be days or weeks in practice).

---

### Likelihood Explanation

**Medium.** Fee adjustments are routine governance operations. Every time `protocolFeeInBPS` is increased — even by a small amount — without a preceding `updateRSETHPrice()` call, the retroactive effect occurs automatically on the next price update. `updateRSETHPrice()` is a public, permissionless function callable by any address, so the settlement can be triggered immediately after the fee change by any external party, making the window of exposure very short but the impact certain.

---

### Recommendation

Call `updateRSETHPrice()` (or its internal equivalent `_updateRsETHPrice()`) inside `setProtocolFeeBps()` before writing the new fee, mirroring the fix applied in the referenced report:

```solidity
function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
    // Settle all yield accrued under the current fee before changing it
    ILRTOracle(contractMap[LRTConstants.LRT_ORACLE]).updateRSETHPrice();

    if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
    protocolFeeInBPS = _protocolFeeInBPS;
    emit UpdateFee(_protocolFeeInBPS);
}
```

This ensures the new fee only applies to yield that accrues **after** the change.

---

### Proof of Concept

**Setup:**
- `rsETHPrice` was last updated 7 days ago at `1.05 ETH/rsETH`
- `rsethSupply = 10,000 rsETH`
- `previousTVL = 10,000 * 1.05 = 10,500 ETH`
- EigenLayer rewards have grown TVL to `10,600 ETH` → `rewardAmount = 100 ETH`
- Current `protocolFeeInBPS = 500` (5%)

**Attack sequence:**

1. Manager calls `LRTConfig.setProtocolFeeBps(1000)` — raises fee to 10%, **no price update**. [1](#0-0) 

2. Any address calls `LRTOracle.updateRSETHPrice()` (public, permissionless). [2](#0-1) 

3. Inside `_updateRsETHPrice()`:
   - `rewardAmount = 10,600 - 10,500 = 100 ETH`
   - `protocolFeeInETH = 100 * 1000 / 10,000 = 10 ETH` ← new 10% rate applied to entire window
   - Correct fee (5% for 7 days of accrual) would have been `5 ETH`
   - **Extra 5 ETH** is minted as rsETH to the treasury, diluting all holders [3](#0-2) 

4. `newRsETHPrice = (10,600 - 10) / 10,000 = 1.059 ETH/rsETH` instead of the correct `1.0595 ETH/rsETH`.

The 5 ETH difference is permanently transferred from rsETH holders to the treasury. The root cause is that `setProtocolFeeBps` does not settle the pending yield window before changing the rate. [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L234-250)
```text
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

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L298-308)
```text
        // mint protocol fee as rsETH if there's a fee to take
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
