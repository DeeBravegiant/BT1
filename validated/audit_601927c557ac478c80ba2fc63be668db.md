Audit Report

## Title
Stale Cross-Chain Oracle Rate Causes Structural rsETH Deficit and Pool Insolvency in L2 Deposit Pools - (`contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary

The L2 deposit pools compute rsETH/wrsETH issuance using a cross-chain oracle rate that is structurally behind the actual L1 rate, because rsETH appreciates continuously while the oracle is updated only periodically. On every deposit, the pool issues more rsETH/wrsETH than the bridged ETH can mint on L1, creating a cumulative deficit with no on-chain backstop. For `RSETHPoolNoWrapper`, the pool's pre-loaded rsETH reserve depletes monotonically. For `RSETHPoolV3` and `RSETHPoolV3ExternalBridge`, wrsETH becomes progressively undercollateralized.

## Finding Description

**Root cause — no staleness enforcement on oracle rate:**

`RSETHPoolNoWrapper.getRate()`, `RSETHPoolV3.getRate()`, and `RSETHPoolV3ExternalBridge.getRate()` all delegate unconditionally to `IOracle(rsETHOracle).getRate()` with no check on `lastUpdated`.

`CrossChainRateReceiver` stores the rate and timestamp when a LayerZero message arrives but exposes no staleness guard in `getRate()`: [1](#0-0) 

`InterimRSETHOracle` does not even track `lastUpdated`; it simply stores whatever the `MANAGER_ROLE` last set: [2](#0-1) 

**Exploit flow — `RSETHPoolNoWrapper`:**

1. User calls `deposit()`. The pool computes `rsETHAmount = amountAfterFee * 1e18 / R_L2` and immediately transfers rsETH from its own balance to the user: [3](#0-2) [4](#0-3) 

2. The collected ETH is later bridged to L1 by a `BRIDGER_ROLE` caller. `L1Vault.depositETHForL1VaultETH()` deposits it into `LRTDepositPool` at the **current L1 rate** `R_L1`: [5](#0-4) 

3. Because rsETH appreciates monotonically, `R_L1 ≥ R_L2` always. The L1 mints `ETH * 1e18 / R_L1` rsETH, which is bridged back to the L2 pool. The pool gave out `ETH * 1e18 / R_L2` rsETH. The per-cycle deficit is:

   `Δ = ETH × (R_L1 − R_L2) / (R_L2 × R_L1)`

   This deficit is never recovered; it accumulates on every deposit cycle.

**Exploit flow — `RSETHPoolV3` / `RSETHPoolV3ExternalBridge`:**

The same rate computation applies. Instead of transferring pre-loaded rsETH, the pool mints wrsETH to the user immediately: [6](#0-5) 

The rsETH backing for that wrsETH arrives from L1 only after bridging. Because fewer rsETH arrive than wrsETH were minted, the wrsETH wrapper's rsETH collateral is structurally short on every deposit.

**Why existing checks are insufficient:**

- The daily minting limit (`limitDailyMint`) caps the *rate* of deficit accumulation but does not prevent it.
- There is no minimum-rate check, no staleness revert, no deficit accounting, and no reserve fund in any pool contract.
- The `InterimRSETHOracle` has no `lastUpdated` field at all, making the lag unbounded. [7](#0-6) 

## Impact Explanation

**Critical — Protocol insolvency.** For `RSETHPoolNoWrapper`, the pool's rsETH reserve depletes monotonically until it cannot fulfill new deposits. For `RSETHPoolV3`/`RSETHPoolV3ExternalBridge`, wrsETH becomes undercollateralized: the total wrsETH outstanding exceeds the rsETH held by the wrapper, so last redeemers receive less rsETH than their wrsETH entitles them to, or redemptions fail entirely. No contract mechanism covers the shortfall. At $100M TVL, 5% APR, and a 24-hour oracle lag, the annualized deficit is approximately $4.9M with no recovery path.

## Likelihood Explanation

**Medium.** The deficit is structural and continuous — it occurs on every ordinary deposit by any unprivileged user, requiring no adversarial action. The rate difference is small per block but cumulative at protocol scale. The `InterimRSETHOracle` path (manual updates) creates larger and unbounded staleness windows. The `CrossChainRateReceiver` path depends on LayerZero message frequency, which can be delayed by network congestion or operator inaction. No attacker capability beyond calling `deposit()` is required.

## Recommendation

1. **Enforce oracle freshness**: Add a `maxStaleness` parameter and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`. Apply to both `CrossChainRateReceiver` and any pool-level oracle wrapper.
2. **Rate-cap on deposit**: Use `min(oracleRate, conservativeFloorRate)` or require the oracle rate to be within a bounded tolerance of the last known L1 rate before issuing rsETH/wrsETH.
3. **Deficit accounting**: Track cumulative rsETH issued vs. rsETH received from L1; pause new deposits when the deficit exceeds a configurable threshold.
4. **Stability reserve**: Allocate a portion of collected fees to a reserve fund that covers the rate-lag deficit.
5. **Add `lastUpdated` to `InterimRSETHOracle`**: The current implementation has no timestamp tracking at all, making staleness detection impossible. [8](#0-7) 

## Proof of Concept

**Setup**: `RSETHPoolNoWrapper` on Arbitrum. `InterimRSETHOracle` last set at `R_L2 = 1.050e18`. Actual L1 rate 24 hours later: `R_L1 = 1.055e18` (~4.7% APR accrual).

**Step 1 — User deposits 100 ETH on L2 (unprivileged call):**
```
rsETHAmount = 100e18 * 1e18 / 1.050e18 = 95.238 rsETH
```
Pool transfers 95.238 rsETH to user from its balance. [9](#0-8) 

**Step 2 — Bridger bridges 100 ETH to L1:**
`L1Vault.depositETHForL1VaultETH()` calls `lrtDepositPool.depositETH(100 ETH)` at current L1 rate `1.055e18`:
```
rsETHMinted = 100e18 * 1e18 / 1.055e18 = 94.787 rsETH
``` [5](#0-4) 

**Step 3 — rsETH bridged back to L2 pool:**
Pool receives 94.787 rsETH but gave out 95.238 rsETH.

**Deficit per cycle: 0.451 rsETH (~$1,350 at $3,000/ETH) on a single 100 ETH deposit.**

**Foundry fork test plan:**
1. Fork Arbitrum mainnet; deploy `RSETHPoolNoWrapper` with `InterimRSETHOracle` at rate `R_L2`.
2. Call `deposit{value: 100 ether}("")`; record `rsETH.balanceOf(pool)` before and after.
3. Simulate L1 deposit at `R_L1 > R_L2`; bridge rsETH back; record pool balance.
4. Assert `pool.rsETH.balanceOf() < pre_deposit_balance` — the deficit is the difference.
5. Repeat N times; assert the pool balance decreases monotonically and eventually reaches zero.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-105)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L14-16)
```text
    /// @notice The current rsETH/ETH rate
    uint256 public rate;

```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L36-51)
```text
    function setRate(uint256 newRate) external onlyRole(MANAGER_ROLE) {
        _setRate(newRate);
    }

    /// @dev Internal function to set the rsETH/ETH rate
    function _setRate(uint256 newRate) internal {
        if (newRate < 1e18) revert InvalidRate();
        rate = newRate;
        emit RateUpdated(newRate);
    }

    /// @notice Get the current rsETH/ETH rate
    /// @return The current rate
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/L1Vault.sol (L150-161)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
