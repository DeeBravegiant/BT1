### Title
Stale `rsETHPrice` Read in Deposit Minting Calculation Without Calling `updateRSETHPrice()` First - (File: contracts/LRTDepositPool.sol)

---

### Summary
`LRTDepositPool.getRsETHAmountToMint()` reads `lrtOracle.rsETHPrice()`, a stored state variable in `LRTOracle`, without first calling `updateRSETHPrice()` to refresh it. The deposit entry points `depositETH()` and `depositAsset()` therefore compute the rsETH mint amount against a potentially stale price, causing depositors to receive more or fewer rsETH tokens than the current protocol state warrants.

---

### Finding Description
`rsETHPrice` is a persistent state variable in `LRTOracle` that is only updated when `_updateRsETHPrice()` is invoked (via the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`). It is never refreshed automatically on reads.

The deposit flow is:

```
depositETH / depositAsset
  → _beforeDeposit
    → getRsETHAmountToMint
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` is a live read from the underlying price feed oracle, while `lrtOracle.rsETHPrice()` returns the last stored value. Neither `depositETH`, `depositAsset`, `_beforeDeposit`, nor `getRsETHAmountToMint` calls `updateRSETHPrice()` before consuming `rsETHPrice`. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

When rewards accrue between two `updateRSETHPrice()` calls, the stored `rsETHPrice` is lower than the true current price. A depositor who calls `depositETH` or `depositAsset` during this window receives more rsETH than the current protocol state entitles them to, because the denominator in the mint formula is artificially low. This dilutes the holdings of all existing rsETH holders: their share of the underlying TVL shrinks without any corresponding action on their part. The protocol does not lose ETH value, but it fails to deliver the promised exchange rate to existing holders. [4](#0-3) 

---

### Likelihood Explanation
**High.** `rsETHPrice` is always stale between successive `updateRSETHPrice()` calls. The protocol accrues EigenLayer rewards continuously, so the true price drifts upward from the stored value in every inter-update interval. Any deposit made during that interval — which is the normal operating state — uses the stale (lower) price. No special conditions or attacker privileges are required; any unprivileged depositor benefits automatically. [5](#0-4) [6](#0-5) 

---

### Recommendation
Call `ILRTOracle(lrtOracleAddress).updateRSETHPrice()` at the start of `depositETH` and `depositAsset` (or inside `_beforeDeposit`) before `getRsETHAmountToMint` reads `rsETHPrice`. This ensures the mint ratio always reflects the current protocol TVL. [7](#0-6) 

---

### Proof of Concept

1. At block N, `updateRSETHPrice()` is called; `rsETHPrice` is stored as `1.001e18` (reflecting current TVL).
2. EigenLayer rewards accrue over the next several hours; the true price rises to `1.002e18`, but `updateRSETHPrice()` has not been called again.
3. Alice calls `depositAsset(stETH, 1e18, 0, "")`.
4. `getRsETHAmountToMint` computes:
   ```
   rsethAmountToMint = (1e18 * 1e18) / 1.001e18  ≈ 0.999001e18 rsETH
   ```
   The correct amount at the true price would be:
   ```
   (1e18 * 1e18) / 1.002e18  ≈ 0.998004e18 rsETH
   ```
5. Alice receives ~997 extra wei of rsETH relative to what the current TVL supports.
6. All existing rsETH holders are diluted by this excess mint; their redeemable share of the underlying TVL decreases without any action on their part.
7. Bob (an existing holder) calls `updateRSETHPrice()` afterward; the new price is computed over a supply that now includes Alice's excess tokens, permanently embedding the dilution. [1](#0-0) [8](#0-7)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L648-670)
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

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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

**File:** contracts/LRTOracle.sol (L214-231)
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
