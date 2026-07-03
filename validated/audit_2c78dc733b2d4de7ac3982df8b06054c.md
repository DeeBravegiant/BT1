### Title
`LRTOracle._updateRsETHPrice()` Uses Spot Balances Manipulable via Flash Loan, Enabling Excess Protocol Fee Minting at Existing Holders' Expense — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function that computes the protocol's TVL using **current spot balances** of the deposit pool. An attacker can flash-loan LST tokens, transfer them directly to `LRTDepositPool` (bypassing `depositAsset`, so no rsETH is minted), and then call `updateRSETHPrice()` in the same transaction. This inflates `totalETHInProtocol` without a corresponding increase in `rsethSupply`, manufacturing a fake reward delta. The protocol then mints excess rsETH as a fee to the treasury, permanently diluting existing rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` computes the new rsETH price and protocol fee as follows:

```
totalETHInProtocol = _getTotalEthInProtocol()   // reads current spot balances
previousTVL        = rsethSupply * rsETHPrice    // uses stored (pre-tx) price
rewardAmount       = totalETHInProtocol - previousTVL
protocolFeeInETH   = rewardAmount * protocolFeeInBPS / 10_000
rsethAmountToMint  = protocolFeeInETH / newRsETHPrice
```

`_getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(asset)` → `getAssetDistributionData(asset)`, which reads:

- **LST assets**: `IERC20(asset).balanceOf(address(this))` — the raw ERC-20 balance of the deposit pool
- **ETH**: `address(this).balance` — the raw ETH balance of the deposit pool

Both are **current spot values** with no time-weighting or snapshot protection.

`LRTDepositPool` exposes a bare `receive() external payable {}` and accepts arbitrary ERC-20 transfers (no accounting guard on direct transfers). An attacker can therefore inflate these balances without minting any rsETH, because `depositAsset` / `depositETH` are the only paths that mint rsETH — a direct transfer does not.

`updateRSETHPrice()` carries only a `whenNotPaused` modifier; it is callable by any EOA or contract:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When the attacker inflates `totalETHInProtocol` by `Δ` without increasing `rsethSupply`:

- A fake `rewardAmount = Δ` is computed.
- `protocolFeeInETH = Δ * feeBPS / 10_000` worth of rsETH is minted to the treasury.
- `rsETHPrice` is updated to the inflated value.
- `highestRsethPrice` is updated to the inflated value.

The minted fee rsETH is a permanent dilution of every existing rsETH holder's share of the underlying ETH. The treasury receives rsETH backed by no real new yield — the value is extracted from existing depositors.

Secondary impact: after the flash loan is repaid, the next `updateRSETHPrice()` call observes `newRsETHPrice < highestRsethPrice` (because the inflated price was stored). If the drop exceeds `pricePercentageLimit`, the downside-protection branch executes, pausing `LRTDepositPool` and `LRTWithdrawalManager` — a temporary freeze of user funds.

---

### Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` is permissionless; no role or key compromise is required.
- Flash loans for major LSTs (stETH, rETH, cbETH) are widely available on Ethereum mainnet.
- The attacker needs only to calibrate the flash-loan size to stay within `pricePercentageLimit` (if set) to avoid a revert; multiple calls across blocks can drain the `maxFeeMintAmountPerDay` limit.
- If `pricePercentageLimit == 0` (unset), there is no per-call cap and a single large flash loan suffices.
- The attacker bears only the flash-loan fee cost; the stolen yield goes to the treasury (not the attacker directly), but the attack is still economically rational as a griefing/dilution vector or if the attacker holds a short position on rsETH.

---

### Recommendation

1. **Snapshot-guard `updateRSETHPrice()`**: Record the TVL at the end of each successful price update and use that snapshot as `previousTVL` in the next call, rather than recomputing it from live balances. This is the direct analog of the recommendation in the reference report ("use previous values").

2. **Alternatively, restrict callers**: Gate `updateRSETHPrice()` to `onlyLRTManager` or a keeper role, removing the permissionless entry point.

3. **Separate accounting from raw balances**: Track deposited LST amounts in an internal mapping updated only through `depositAsset` / `depositETH`, rather than reading `IERC20(asset).balanceOf(address(this))` and `address(this).balance` directly. This prevents direct-transfer inflation.

---

### Proof of Concept

```
Block N (single transaction):

1. Attacker flash-loans 10,000 stETH from Aave/Balancer.

2. Attacker calls stETH.transfer(LRTDepositPool, 10_000e18).
   - LRTDepositPool.getAssetDistributionData(stETH).assetLyingInDepositPool
     increases by 10_000e18.
   - rsETH.totalSupply() is UNCHANGED (no depositAsset call).

3. Attacker calls LRTOracle.updateRSETHPrice().
   - _getTotalEthInProtocol() returns real_TVL + 10_000 * stETH_price_in_ETH.
   - previousTVL = rsethSupply * rsETHPrice  (unchanged supply, old price).
   - rewardAmount = 10_000 * stETH_price_in_ETH  (entirely fake).
   - protocolFeeInETH = rewardAmount * feeBPS / 10_000.
   - rsethAmountToMintAsProtocolFee is minted to treasury.
   - rsETHPrice and highestRsethPrice are updated to the inflated value.

4. Attacker repays 10,000 stETH flash loan.

Result:
- Treasury holds excess rsETH minted against no real yield.
- All pre-existing rsETH holders are diluted by the minted fee amount.
- highestRsethPrice is now set to the inflated value; the next honest
  updateRSETHPrice() call will see a price drop and may trigger a protocol pause.
```

**Key code references:**

- Permissionless entry point: [1](#0-0) 
- Spot-balance TVL computation: [2](#0-1) 
- Raw ERC-20 balance read (LST): [3](#0-2) 
- Raw ETH balance read: [4](#0-3) 
- Fake-reward fee minting: [5](#0-4) 
- Fee rsETH mint to treasury: [6](#0-5) 
- Downside-protection pause branch: [7](#0-6) 
- Bare `receive()` on deposit pool: [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L299-307)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
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

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-448)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```
