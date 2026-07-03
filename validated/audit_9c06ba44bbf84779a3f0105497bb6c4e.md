### Title
`LRTDepositPool.depositAsset()` / `depositETH()` Use Stale Cached `rsETHPrice` Without Prior `updateRSETHPrice()` Call, Enabling Yield Theft from Existing Holders — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a stored state variable that must be explicitly refreshed by calling `updateRSETHPrice()`. Neither `LRTDepositPool.depositAsset()` nor `depositETH()` calls `updateRSETHPrice()` before computing the rsETH mint amount. When rewards have accrued since the last price update, the stored `rsETHPrice` is lower than the true value, causing depositors to receive more rsETH than their fair share and diluting existing holders' yield.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in the state variable `rsETHPrice`:

```solidity
// LRTOracle.sol:28
uint256 public override rsETHPrice;
```

This value is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is explicitly called:

```solidity
// LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` recomputes the price from live TVL data (`_getTotalEthInProtocol()`) and writes the result back to `rsETHPrice`. Between calls, the stored value is stale.

`LRTDepositPool.depositAsset()` and `depositETH()` both call `_beforeDeposit()`, which calls `getRsETHAmountToMint()`:

```solidity
// LRTDepositPool.sol:506-521
function getRsETHAmountToMint(address asset, uint256 amount) public view override
    returns (uint256 rsethAmountToMint)
{
    address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
    ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
    rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
}
```

`lrtOracle.rsETHPrice()` reads the **stored** (potentially stale) value. Neither `depositAsset()` nor `depositETH()` calls `updateRSETHPrice()` before this computation. The deposit flow is:

```
depositAsset() → _beforeDeposit() → getRsETHAmountToMint() → lrtOracle.rsETHPrice()  ← stale read
```

This is the direct analog to the Malt report: a cached value (`globalIC` / `rsETHPrice`) is read in a critical calculation without first syncing it, even though the underlying state has changed.

---

### Impact Explanation

When ETH staking rewards accrue or LST prices appreciate between `updateRSETHPrice()` calls, the true TVL rises but `rsETHPrice` remains at its last-recorded (lower) value. The mint formula divides by this stale-low price, yielding **more rsETH than the depositor's ETH contribution warrants**. The excess rsETH represents a claim on protocol TVL that was earned by existing holders. This is a direct theft of unclaimed yield from all current rsETH holders.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

`updateRSETHPrice()` is called off-chain by keepers/managers on a periodic schedule (not atomically within every deposit). Any deposit made in the window between reward accrual and the next price update exploits the stale price. This window is routine and predictable. An unprivileged depositor can monitor the mempool or on-chain reward events and time their deposit to maximise the stale-price advantage. No special privileges are required.

---

### Recommendation

Call `lrtOracle.updateRSETHPrice()` at the start of `depositAsset()` and `depositETH()` (or inside `_beforeDeposit()`) before `getRsETHAmountToMint()` is invoked, so the mint calculation always uses a fresh price reflecting current TVL.

```solidity
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    internal returns (uint256 rsethAmountToMint)
{
    // Sync price before computing mint amount
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();

    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    // ... rest of checks
}
```

---

### Proof of Concept

1. At time T, `rsETHPrice = 1.00 ETH` (last updated). Protocol TVL = 1000 ETH, rsETH supply = 1000.
2. Staking rewards of 10 ETH accrue. True TVL = 1010 ETH. True price = 1.01 ETH. `rsETHPrice` is NOT updated.
3. Attacker calls `depositAsset(stETH, 10 ETH, ...)`.
4. `getRsETHAmountToMint` computes: `(10e18 * 1e18) / 1.00e18 = 10 rsETH` (using stale price).
5. Correct amount at true price: `10e18 / 1.01e18 ≈ 9.9 rsETH`.
6. Attacker receives `~0.1 rsETH` excess — value extracted from existing holders' accrued yield.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-117)
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

    /// @notice helps user stake LST to the protocol
    /// @param asset LST asset address to stake
    /// @param depositAmount LST asset amount to stake
    /// @param minRSETHAmountExpected Minimum amount of rseth to receive
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
