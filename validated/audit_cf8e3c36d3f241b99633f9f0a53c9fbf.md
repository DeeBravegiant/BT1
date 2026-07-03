### Title
`addNewSupportedAsset()` does not atomically register a price oracle, permanently breaking `updateRSETHPrice()` until a separate admin call is made - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTConfig.addNewSupportedAsset()` appends a new asset to `supportedAssetList` but does **not** set a corresponding entry in `LRTOracle.assetPriceOracle`. `LRTOracle._getTotalEthInProtocol()` unconditionally iterates over every entry in `supportedAssetList` and calls `getAssetPrice(asset)`, which reverts via the `onlySupportedOracle` modifier when `assetPriceOracle[asset] == address(0)`. The result is that `updateRSETHPrice()` is completely broken for every caller — privileged or not — until a separate `updatePriceOracleFor()` call is made by an LRT admin. During this window the stored `rsETHPrice` grows stale, and any depositor can mint rsETH at a below-market rate, extracting yield from existing holders.

---

### Finding Description

**`LRTConfig.addNewSupportedAsset()` — list updated, oracle mapping not updated** [1](#0-0) 

`_addNewSupportedAsset()` sets `isSupportedAsset[asset] = true`, pushes `asset` onto `supportedAssetList`, and sets `depositLimitByAsset[asset]`. It does **not** touch `LRTOracle.assetPriceOracle[asset]`, which remains `address(0)`.

**`LRTOracle._getTotalEthInProtocol()` — iterates the full list, calls `getAssetPrice()` for every entry** [2](#0-1) 

Every element of `lrtConfig.getSupportedAssetList()` is passed to `getAssetPrice(asset)`.

**`LRTOracle.getAssetPrice()` — reverts when oracle is unset** [3](#0-2) [4](#0-3) 

The `onlySupportedOracle` modifier reverts with `AssetOracleNotSupported` when `assetPriceOracle[asset] == address(0)`. Because `_getTotalEthInProtocol()` calls this for every supported asset, a single asset with no oracle causes the entire function to revert.

**`updateRSETHPrice()` — the only path to refresh the stored price** [5](#0-4) 

`updateRSETHPrice()` is a public function; it calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()`. Once the revert chain is triggered, **no one** — including the manager via `updateRSETHPriceAsManager()` — can update `rsETHPrice`.

**Deposit flow uses the stale stored `rsETHPrice` directly** [6](#0-5) [4](#0-3) 

`getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()` (the stored value). Deposits are **not** blocked; they proceed using the frozen, below-market price.

**The two operations that must be atomic are gated by different roles** [7](#0-6) [8](#0-7) 

`addNewSupportedAsset()` requires `TIME_LOCK_ROLE`; `updatePriceOracleFor()` requires `onlyLRTAdmin`. These are separate roles and may be held by separate multi-sigs or subject to independent governance delays, making the window between the two calls non-trivial.

---

### Impact Explanation

During the window between `addNewSupportedAsset()` and `updatePriceOracleFor()`:

1. `updateRSETHPrice()` reverts for every caller — the stored `rsETHPrice` is frozen at its last value.
2. As EigenLayer rewards accrue, the true backing per rsETH increases, but the stored price does not.
3. Any depositor calling `depositETH()` or `depositAsset()` mints rsETH at the stale (lower) price, receiving more rsETH than the current backing warrants.
4. This dilutes existing rsETH holders, transferring their accrued yield to the new depositor.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

`addNewSupportedAsset()` is a routine governance operation (adding a new LST). The follow-up `updatePriceOracleFor()` call is a separate transaction by a potentially different key-holder. Multi-sig execution delays, governance queues, or simple operational oversight can leave the window open for hours to days. The exploit requires no special access — any depositor can act the moment the new asset appears in `supportedAssetList`.

**Likelihood: Medium.**

---

### Recommendation

Enforce atomicity at the point of asset registration. Two options:

1. **Require the oracle at registration time**: Extend `addNewSupportedAsset()` (or `_addNewSupportedAsset()`) to accept a `priceOracle` address and call `LRTOracle.updatePriceOracleFor(asset, priceOracle)` in the same transaction.

2. **Make `_getTotalEthInProtocol()` defensive**: Skip assets whose `assetPriceOracle` is `address(0)` rather than reverting, so a misconfigured asset degrades gracefully instead of bricking the price update for all assets.

Option 1 is preferred because it preserves the invariant that every supported asset always has a valid oracle.

---

### Proof of Concept

```
1. Protocol is live; rsETHPrice = 1.01e18 (rewards have accrued).

2. TIME_LOCK_ROLE calls:
       LRTConfig.addNewSupportedAsset(newLST, depositLimit)
   → newLST is now in supportedAssetList; assetPriceOracle[newLST] == address(0).

3. Anyone calls LRTOracle.updateRSETHPrice().
   → _getTotalEthInProtocol() iterates supportedAssetList.
   → getAssetPrice(newLST) hits onlySupportedOracle → reverts.
   → rsETHPrice remains frozen at 1.01e18 (or whatever the last value was).

4. Actual backing per rsETH grows to, say, 1.015e18 as more rewards arrive.

5. Attacker calls LRTDepositPool.depositETH{ value: 100 ether }(0, "").
   → getRsETHAmountToMint uses rsETHPrice = 1.01e18 (stale).
   → Attacker receives 100e18 / 1.01e18 ≈ 99.01 rsETH.
   → Fair amount at 1.015e18 would be ≈ 98.52 rsETH.
   → Attacker gains ~0.49 rsETH at the expense of existing holders.

6. LRT admin eventually calls LRTOracle.updatePriceOracleFor(newLST, oracle).
   → updateRSETHPrice() succeeds again; price jumps to reflect dilution.
```

### Citations

**File:** contracts/LRTConfig.sol (L99-101)
```text
    function addNewSupportedAsset(address asset, uint256 depositLimit) external onlyRole(LRTConstants.TIME_LOCK_ROLE) {
        _addNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTConfig.sol (L106-118)
```text
    function _addNewSupportedAsset(address asset, uint256 depositLimit) private {
        UtilLib.checkNonZeroAddress(asset);
        if (depositLimit == 0) {
            revert InvalidDepositLimit();
        }
        if (isSupportedAsset[asset]) {
            revert AssetAlreadySupported();
        }
        isSupportedAsset[asset] = true;
        supportedAssetList.push(asset);
        depositLimitByAsset[asset] = depositLimit;
        emit AddedNewSupportedAsset(asset, depositLimit);
    }
```

**File:** contracts/LRTOracle.sol (L40-45)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L113-119)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
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

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```
