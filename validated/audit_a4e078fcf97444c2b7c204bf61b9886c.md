### Title
Publicly Callable `updateRSETHPrice()` Allows Any Caller to Permanently Suppress Protocol Fee Collection - (File: contracts/LRTOracle.sol)

---

### Summary

`updateRSETHPrice()` in `LRTOracle` carries no access control and can be invoked by any address. The protocol fee is only collected when `totalETHInProtocol > previousTVL` (strict `>`). Because each successful call writes the freshly computed price back to `rsETHPrice`, the very next call's `previousTVL` equals the current `totalETHInProtocol`, collapsing the strict-greater-than condition to equality and yielding zero fee. Any unprivileged caller who front-runs reward accumulation by calling `updateRSETHPrice()` repeatedly can permanently prevent the protocol treasury from receiving its fee share.

---

### Finding Description

`updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard — no role check of any kind: [1](#0-0) 

Inside `_updateRsETHPrice()`, the protocol fee gate is a strict `>` comparison: [2](#0-1) 

`previousTVL` is computed as `rsethSupply × rsETHPrice` (the stored price from the last update). After the call completes, `rsETHPrice` is overwritten with `newRsETHPrice = totalETHInProtocol / rsethSupply`: [3](#0-2) [4](#0-3) 

Therefore, on the very next call:

```
previousTVL = rsethSupply × (totalETHInProtocol / rsethSupply)
            ≈ totalETHInProtocol          (integer-division rounding aside)
```

The condition `totalETHInProtocol > previousTVL` evaluates to `false`, `protocolFeeInETH` stays `0`, and no fee is minted to the treasury: [5](#0-4) 

This is structurally identical to the reported vulnerability: the fee gate uses a strict inequality (`>`), leaving the equality case unhandled. An unprivileged caller exploits this by calling `updateRSETHPrice()` before any meaningful reward delta accumulates, keeping `totalETHInProtocol ≈ previousTVL` indefinitely.

The attack is repeatable with no special privileges — only gas — and can be sustained across every block.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

The protocol fee is minted as rsETH to the treasury address: [6](#0-5) 

By suppressing every fee-collection event, the treasury permanently receives zero rsETH. An attacker who holds rsETH also benefits directly: fee minting dilutes existing rsETH holders, so preventing it preserves the attacker's proportional share of the underlying ETH. The attacker thus extracts value that would otherwise flow to the treasury.

---

### Likelihood Explanation

**Medium.** No special role, no capital lock-up, and no flash loan is required. The only cost is gas per call. Any rsETH holder with a position large enough that the avoided dilution exceeds gas costs has a clear economic incentive to run this attack continuously. The attack is also trivially automatable via a keeper bot.

---

### Recommendation

- **Add a minimum update interval**: require `block.timestamp >= lastPriceUpdateTimestamp + MIN_UPDATE_INTERVAL` inside `updateRSETHPrice()`, preventing sub-period calls from collapsing the fee window.
- **Restrict public access**: gate `updateRSETHPrice()` behind `onlyLRTOperator` or a similar role, mirroring the pattern already used for `updateRSETHPriceAsManager()`.
- **Accumulate rewards before snapshotting**: compute `previousTVL` from a time-weighted average rather than the last stored price, so a single call cannot zero out the reward delta.

---

### Proof of Concept

1. Protocol accumulates staking rewards; `totalETHInProtocol` grows above `previousTVL`.
2. Attacker (an rsETH holder) calls `updateRSETHPrice()` before the operator does.
3. `rsETHPrice` is updated to `totalETHInProtocol / rsethSupply`; `protocolFeeInETH = 0` because the strict `>` check fails (equality case).
4. Treasury receives no rsETH fee for this reward period.
5. On the next block, `previousTVL = rsethSupply × newRsETHPrice ≈ totalETHInProtocol` again.
6. Attacker repeats step 2 indefinitely, permanently suppressing all fee collection.
7. The attacker's rsETH balance retains its full proportional claim on the underlying ETH, effectively capturing the value that should have been minted to the treasury.

### Citations

**File:** contracts/LRTOracle.sol (L85-89)
```text
    /// @notice updates RSETH/ETH exchange rate
    /// @dev calculates rsETH price based on stakedAsset value received from EigenLayer
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

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L298-311)
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
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
