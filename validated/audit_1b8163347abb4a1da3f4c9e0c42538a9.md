### Title
Stale `rsETHPrice` Used in Deposit and Withdrawal Calculations Leads to Yield Theft — (`contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint` and `LRTWithdrawalManager.getExpectedAssetAmount` both read `lrtOracle.rsETHPrice()`, which is a **stored state variable** that is only updated when `updateRSETHPrice()` is explicitly called. Between updates, the price is stale. When staking rewards accrue and the true rsETH price rises above the stored value, depositors receive more rsETH than they deserve, stealing yield from existing holders.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in a public state variable `rsETHPrice`: [1](#0-0) 

This value is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is called, which internally calls `_updateRsETHPrice()`: [2](#0-1) 

The actual current price is computed inside `_updateRsETHPrice()` using `_getTotalEthInProtocol()`, which sums all staked assets at their current oracle prices: [3](#0-2) 

However, `_getTotalEthInProtocol()` is `private` and has no public view equivalent. There is no `getCurrentRsETHPrice()` view function exposed through `ILRTOracle`: [4](#0-3) 

**Deposit path** — `LRTDepositPool.getRsETHAmountToMint` reads the stale stored price: [5](#0-4) 

This is called unconditionally inside `_beforeDeposit`, which is called by `depositAsset` and `depositETH` with no prior call to `updateRSETHPrice()`: [6](#0-5) 

**Withdrawal path** — `LRTWithdrawalManager.getExpectedAssetAmount` also reads the stale stored price: [7](#0-6) 

This is called inside `initiateWithdrawal` with no prior price refresh: [8](#0-7) 

The `instantWithdrawal` path has the same issue: [9](#0-8) 

---

### Impact Explanation

When ETH staking rewards or EigenLayer rewards accrue, the true rsETH/ETH rate rises above the stored `rsETHPrice`. During this window:

- **Depositors** compute `rsethAmountToMint = (depositAmount × assetPrice) / rsETHPrice`. A stale (lower) `rsETHPrice` inflates the denominator's inverse, minting **more rsETH than deserved**. The new depositor captures a share of accrued yield that belongs to existing holders — this is direct theft of unclaimed yield.
- **Withdrawers** compute `underlyingToReceive = rsETHAmount × rsETHPrice / assetPrice`. A stale (lower) `rsETHPrice` gives them **fewer assets than deserved**, harming them.

The deposit scenario is the critical one: an attacker can monitor the gap between the stored price and the true price (computable off-chain from on-chain balances), then deposit a large amount just before `updateRSETHPrice()` is called, extracting accrued yield from all existing rsETH holders.

**Impact**: High — Theft of unclaimed yield.

---

### Likelihood Explanation

`updateRSETHPrice()` is a permissionless function but is not called atomically with deposits or withdrawals. In practice, oracle updates are triggered off-chain (by bots or managers) on a periodic schedule. Any gap between updates — which can span hours — creates a window where the price is stale. The gap is predictable and observable on-chain, making this straightforwardly exploitable by any depositor.

**Likelihood**: High — no special access required; the stale window is always present between oracle updates.

---

### Recommendation

Add a public view function to `LRTOracle` that computes the current rsETH price on-the-fly (mirroring the logic in `_updateRsETHPrice` / `_getTotalEthInProtocol`) without updating state. Expose it through `ILRTOracle`. Then, `getRsETHAmountToMint` and `getExpectedAssetAmount` should call this live-price function instead of reading the stored `rsETHPrice`. Alternatively, call `updateRSETHPrice()` atomically at the start of `depositAsset`, `depositETH`, and `initiateWithdrawal` before any price-dependent calculation.

---

### Proof of Concept

1. Assume `rsETHPrice` stored = `1.00 ETH` (last updated 12 hours ago).
2. Since then, staking rewards have accrued; true price = `1.005 ETH` (0.5% yield).
3. Attacker calls `depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint` computes: `(1000e18 × 1e18) / 1.00e18 = 1000 rsETH`.
5. True fair amount at current price: `(1000e18 × 1e18) / 1.005e18 ≈ 995.02 rsETH`.
6. Attacker receives `~4.98 rsETH` excess — accrued yield stolen from existing holders.
7. `updateRSETHPrice()` is called; price updates to `1.005 ETH`, but the attacker's excess rsETH is already minted and dilutes all prior holders. [10](#0-9) [11](#0-10)

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

**File:** contracts/LRTOracle.sol (L214-232)
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

```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/interfaces/ILRTOracle.sol (L29-32)
```text
    function getAssetPrice(address asset) external view returns (uint256);
    function assetPriceOracle(address asset) external view returns (address);
    function rsETHPrice() external view returns (uint256);
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

**File:** contracts/LRTDepositPool.sol (L648-666)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
