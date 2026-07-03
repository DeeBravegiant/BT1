### Title
Donated tokens inflate `getTotalAssetDeposits` via raw `balanceOf`, enabling deposit-limit DoS and rsETH price manipulation — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.getAssetDistributionData()` measures the deposit pool's asset balance using a raw `IERC20(asset).balanceOf(address(this))` call. Because the contract has an open `receive()` fallback and accepts arbitrary ERC-20 transfers, any unprivileged actor can donate tokens directly to the pool, inflating the reported total without going through the deposit flow. This inflated total propagates into `getAssetCurrentLimit()` (blocking legitimate deposits) and into `LRTOracle._getTotalEthInProtocol()` (manipulating the rsETH price when the public `updateRSETHPrice()` is called).

---

### Finding Description

**Step 1 — Raw balance read in `getAssetDistributionData`** [1](#0-0) 

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

There is no accounting variable that tracks only legitimately deposited tokens; the function reads the live ERC-20 balance, which includes any tokens sent directly to the contract address.

**Step 2 — Inflated balance flows into `getTotalAssetDeposits`** [2](#0-1) 

`getTotalAssetDeposits` sums `assetLyingInDepositPool` (from the raw `balanceOf`) with NDC balances, EigenLayer stakes, and the unstaking vault balance. A donation directly inflates this sum.

**Step 3a — Deposit limit DoS via `getAssetCurrentLimit`** [3](#0-2) 

```solidity
function getAssetCurrentLimit(address asset) public view override returns (uint256) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
        return 0;
    }
    return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
}
```

If an attacker donates enough tokens to push `totalAssetDeposits` above `depositLimitByAsset`, `getAssetCurrentLimit` returns 0. The `_beforeDeposit` path (called by both `depositAsset` and `depositETH`) checks this limit and reverts with `MaximumDepositLimitReached`, blocking all new deposits for that asset.

**Step 3b — rsETH price manipulation via `_getTotalEthInProtocol`** [4](#0-3) 

```solidity
function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    ...
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    ...
}
```

The donated tokens inflate `totalETHInProtocol`, which is used in `_updateRsETHPrice()` to compute the new rsETH price: [5](#0-4) 

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`updateRSETHPrice()` is a **public, permissionless function**: [6](#0-5) 

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

An attacker can donate tokens and immediately call `updateRSETHPrice()`, causing the rsETH price to be set higher than the true backing. New depositors then receive fewer rsETH tokens than they are entitled to (since `getRsETHAmountToMint` divides by the inflated `rsETHPrice`): [7](#0-6) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

The same raw-balance issue applies to ETH: `getETHDistributionData` uses `address(this).balance`, and the contract has an open `receive()` fallback. [8](#0-7) 

---

### Impact Explanation

**Deposit-limit DoS (Medium — Temporary freezing of funds):** An attacker donates a small amount of tokens (just enough to push `totalAssetDeposits` over `depositLimitByAsset`) and all subsequent `depositAsset` / `depositETH` calls for that asset revert. The freeze persists until an admin raises the deposit limit. The attacker loses the donated tokens but the cost is bounded by the remaining deposit capacity, which may be small.

**rsETH price manipulation (Low–Medium — Contract fails to deliver promised returns):** By donating tokens and calling the public `updateRSETHPrice()`, the attacker inflates the rsETH price. New depositors receive fewer rsETH tokens than the true exchange rate warrants. The `pricePercentageLimit` guard partially mitigates large single-step manipulations, but if `pricePercentageLimit == 0` (unset) or the donation is sized to stay within the threshold, the manipulation succeeds silently.

---

### Likelihood Explanation

The attack requires only a direct ERC-20 transfer to the `LRTDepositPool` address followed by a public function call — no special role, no flash loan, no complex setup. The deposit limit for each asset is a fixed on-chain value visible to anyone, so the exact donation amount needed to trigger the DoS is trivially computable. Likelihood is **high** for the DoS vector and **medium** for the price manipulation vector (constrained by `pricePercentageLimit` when set).

---

### Recommendation

Replace the raw `balanceOf` reads with an internal accounting variable that is incremented only through the controlled deposit/transfer paths:

```solidity
mapping(address => uint256) internal _assetBalance; // tracked internally

// In depositAsset:
_assetBalance[asset] += depositAmount;

// In getAssetDistributionData:
assetLyingInDepositPool = _assetBalance[asset]; // not balanceOf
```

Alternatively, compute `assetLyingInDepositPool` as `balanceOf - sum_of_untracked_donations` by tracking total legitimate inflows. For ETH, replace `address(this).balance` with a similarly tracked variable.

---

### Proof of Concept

```
1. Protocol has stETH deposit limit = 100_000 ether; current deposits = 99_999 ether.
2. Attacker calls stETH.transfer(address(lrtDepositPool), 2 ether).
   - No approval from the protocol needed; this is a direct ERC-20 transfer.
3. getAssetDistributionData() now returns assetLyingInDepositPool = 2 ether extra.
4. getTotalAssetDeposits(stETH) = 100_001 ether > 100_000 ether limit.
5. getAssetCurrentLimit(stETH) returns 0.
6. Any user calling depositAsset(stETH, ...) hits MaximumDepositLimitReached and reverts.
7. Attacker also calls updateRSETHPrice() (public).
   - _getTotalEthInProtocol() includes the donated 2 ether.
   - rsETHPrice is set above the true backing value.
   - Next depositor calling depositAsset receives fewer rsETH tokens than deserved.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L331-343)
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
```
