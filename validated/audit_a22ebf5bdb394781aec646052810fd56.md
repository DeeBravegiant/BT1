### Title
Stale `rsETHPrice` Used in Deposit Minting Without Prior `updateRSETHPrice()` Call — (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset()` and `depositETH()` compute the rsETH amount to mint using `lrtOracle.rsETHPrice()`, a stored state variable in `LRTOracle` that is only updated when `updateRSETHPrice()` is explicitly called. Neither the deposit entry points nor the internal `getRsETHAmountToMint()` helper trigger a price refresh before reading `rsETHPrice`. When rewards have accrued since the last keeper update, `rsETHPrice` is stale-low relative to the true protocol value, causing depositors to receive more rsETH than their deposit is worth and diluting existing holders' yield.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a persistent state variable updated only by explicit calls to `updateRSETHPrice()` (public) or `updateRSETHPriceAsManager()` (manager-only). The price reflects total ETH in the protocol divided by rsETH supply at the time of the last update.

```
// LRTOracle.sol
uint256 public override rsETHPrice;   // cached, not auto-refreshed
```

The deposit flow reads this cached value directly:

```
// LRTDepositPool.sol – getRsETHAmountToMint()
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`getRsETHAmountToMint()` is a `view` function — it cannot call `updateRSETHPrice()`. The calling chain `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` never invokes `updateRSETHPrice()` before reading `rsETHPrice`.

Between keeper updates, EigenLayer restaking rewards and LST rebases cause the true protocol TVL to grow. The actual rsETH price rises above the stored `rsETHPrice`. Any deposit made during this window uses the stale (lower) price, minting more rsETH than the deposited value warrants:

```
rsethAmountToMint = depositValue / staleRsETHPrice   // > depositValue / trueRsETHPrice
```

The excess rsETH represents a claim on protocol TVL that was earned by existing holders, not by the new depositor.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate yield as the protocol's TVL grows relative to rsETH supply. When a depositor mints rsETH at a stale-low price, they receive a larger share of the pool than their deposit justifies. The difference is extracted from the unrealised yield that belongs to prior holders. The magnitude scales with (a) the time elapsed since the last `updateRSETHPrice()` call and (b) the deposit size. In a protocol managing hundreds of millions in TVL with daily EigenLayer rewards, even a few hours of stale price represents a meaningful extractable amount.

---

### Likelihood Explanation

**Medium.**

`updateRSETHPrice()` is called by an off-chain keeper on a periodic schedule (not on every block). The stale window is always non-zero. Any depositor — without any privileged access — can observe the last update timestamp on-chain, estimate accrued rewards, and time their deposit to maximise the stale-price advantage. The `pricePercentageLimit` guard in `_updateRsETHPrice()` can additionally delay keeper updates when price movement exceeds the threshold, extending the exploitable window further.

---

### Recommendation

Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositAsset()` and `depositETH()` before the rsETH mint amount is computed, mirroring the fix pattern from the referenced report:

```solidity
function depositAsset(
    address asset,
    uint256 depositAmount,
    uint256 minRSETHAmountExpected,
    string calldata referralId
) external nonReentrant whenNotPaused onlySupportedERC20Token(asset) {
+   ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
    ...
}
```

Apply the same change to `depositETH()`. This ensures the mint calculation always uses the freshest possible rsETH price, eliminating the stale-price dilution vector.

---

### Proof of Concept

1. Assume `rsETHPrice` was last updated 6 hours ago at `1.010 ETH/rsETH`. Since then, EigenLayer rewards have pushed the true price to `1.011 ETH/rsETH` (≈0.1% increase).
2. Attacker calls `depositAsset(stETH, 1000e18, 0, "")` without calling `updateRSETHPrice()` first.
3. `getRsETHAmountToMint` computes: `1000e18 * 1e18 / 1.010e18 ≈ 990.099 rsETH` (using stale price).
4. True fair amount: `1000e18 * 1e18 / 1.011e18 ≈ 989.12 rsETH`.
5. Attacker receives ≈0.979 excess rsETH (≈$1–2 per $1 000 deposit at current rates), extracted from existing holders' accrued yield.
6. Attacker immediately calls `updateRSETHPrice()` (or waits for the keeper), then redeems at the updated price, capturing the difference. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-251)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

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

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
