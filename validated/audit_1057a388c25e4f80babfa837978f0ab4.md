### Title
Stale `rsETHPrice` Used for rsETH Minting Dilutes Existing Holder Yield - (File: contracts/LRTDepositPool.sol / contracts/LRTOracle.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.rsETHPrice()`, which reads a **stored/cached** state variable that is only updated when `updateRSETHPrice()` is explicitly called. Between updates, the stored price does not reflect accrued yield, causing new depositors to receive excess rsETH and diluting existing holders' yield — a direct analog to M-11's use of `exchangeRateStored` instead of `exchangeRateCurrent`.

### Finding Description
`LRTOracle.rsETHPrice` is a stored state variable updated only by explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`. [1](#0-0) 

The deposit flow in `LRTDepositPool` reads this stored value directly without refreshing it: [2](#0-1) 

`getRsETHAmountToMint` computes:
```
rsethAmountToMint = (amount * assetPrice) / lrtOracle.rsETHPrice()
``` [3](#0-2) 

The real current price is computed inside `_updateRsETHPrice()` by reading live TVL via `_getTotalEthInProtocol()`, which calls `IStrategy.sharesToUnderlyingView()` and live asset price oracles. This live computation is **never triggered** during the deposit path. [4](#0-3) 

Between `updateRSETHPrice()` calls, yield accrues in EigenLayer strategies (e.g., stETH rebases increase `_tokenBalance()` in the strategy, rETH/ETH oracle price rises). The real rsETH/ETH price increases, but `rsETHPrice` remains at the last stored value. Since `rsethAmountToMint = amount * assetPrice / rsETHPrice`, a stale (lower) `rsETHPrice` causes new depositors to receive **more rsETH than they should**.

The `depositETH` and `depositAsset` entry points both flow through `_beforeDeposit` → `getRsETHAmountToMint` with no price refresh: [5](#0-4) 

### Impact Explanation
Every deposit made while `rsETHPrice` is stale mints excess rsETH. This excess dilutes the share of the protocol TVL held by existing rsETH holders, reducing the yield they receive. The effect is systematic: yield accrues continuously in EigenLayer strategies, so `rsETHPrice` is always at least slightly stale between updates. Existing rsETH holders consistently receive less yield than they are entitled to. This matches the M-11 impact class: **contract fails to deliver promised returns to existing holders, but does not lose principal value**.

Impact: **Low** — Contract fails to deliver promised returns, but doesn't lose value.

### Likelihood Explanation
This occurs on every deposit made between `updateRSETHPrice()` calls. The protocol does not enforce a price refresh before minting. `updateRSETHPrice()` is public and callable by anyone, but there is no on-chain requirement to call it before depositing. The effect is proportional to the time elapsed since the last update and the yield rate of the underlying assets.

### Recommendation
Before computing `rsethAmountToMint` in `getRsETHAmountToMint`, compute the current rsETH price on-the-fly using the live TVL and rsETH supply, rather than reading the stored `rsETHPrice`. Alternatively, enforce that `updateRSETHPrice()` is called atomically before any deposit that uses `rsETHPrice` for minting. A read-only version of `_getTotalEthInProtocol()` already exists and can be used to compute the live price without state changes (excluding the fee-minting side effect).

### Proof of Concept
1. At time T, `updateRSETHPrice()` is called. `rsETHPrice = 1.05e18` (reflecting accrued yield).
2. Time passes; stETH rebases, increasing the strategy's `_tokenBalance()`. Real rsETH price is now `1.06e18`.
3. Alice calls `depositETH(0, "")` with 1 ETH. `getRsETHAmountToMint` computes: `1e18 * 1e18 / 1.05e18 = 0.952 rsETH` (using stale price). Correct amount at real price: `1e18 * 1e18 / 1.06e18 = 0.943 rsETH`.
4. Alice receives ~0.009 excess rsETH. This excess dilutes all existing rsETH holders proportionally.
5. When `updateRSETHPrice()` is next called, the new price is computed on the now-larger rsETH supply, yielding a lower price than it would have been — existing holders' yield is permanently reduced by the dilution. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
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
