### Title
`SfrxETHPriceOracle` Returns sfrxETH/frxETH Rate Instead of sfrxETH/ETH, Mispricing sfrxETH When frxETH Depegs - (File: contracts/oracles/SfrxETHPriceOracle.sol)

---

### Summary

`SfrxETHPriceOracle.getAssetPrice()` calls `sfrxETH.pricePerShare()`, which returns the amount of **frxETH** redeemable per sfrxETH share — not the amount of **ETH**. The oracle treats frxETH as canonical ETH. When frxETH trades at a discount to ETH, the oracle overstates the true ETH value of sfrxETH held in the protocol, inflating `rsETHPrice` and enabling depositors to extract more ETH value than they contributed.

---

### Finding Description

`SfrxETHPriceOracle` is the price oracle for sfrxETH used by `LRTOracle` to compute the total ETH value of protocol holdings. [1](#0-0) 

The interface comment reads "How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD" — but this is incorrect. `pricePerShare()` is an ERC4626 function that returns the amount of the vault's **underlying asset** (frxETH) per share (sfrxETH). frxETH is a synthetic ETH derivative issued by Frax Finance that is *pegged* to ETH but is not ETH itself and can trade at a discount. [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(sfrxETH)` for every supported asset and multiplies by the total deposited amount to compute the protocol's ETH TVL: [3](#0-2) 

This TVL feeds directly into `rsETHPrice`: [4](#0-3) 

Because `pricePerShare()` returns sfrxETH/frxETH (not sfrxETH/ETH), the oracle implicitly assumes frxETH ≡ 1 ETH at all times. This is the same structural flaw as the PegOracle pattern in H-03: two pegged assets are treated symmetrically, with neither being the canonical reference.

---

### Impact Explanation

When frxETH depegs below ETH (e.g., frxETH = 0.95 ETH):

- `getAssetPrice(sfrxETH)` returns `pricePerShare()` ≈ 1.05 frxETH/sfrxETH, which the oracle treats as 1.05 ETH/sfrxETH.
- True ETH value of sfrxETH = 1.05 × 0.95 = ~0.9975 ETH/sfrxETH.
- `totalETHInProtocol` is overstated by the full frxETH depeg magnitude across all sfrxETH holdings.
- `rsETHPrice` is inflated.
- A depositor who deposits sfrxETH (worth 0.9975 ETH) receives rsETH priced at 1.05 ETH/sfrxETH, then redeems rsETH for ETH/other LSTs at the inflated rate — extracting more ETH than deposited at the expense of other rsETH holders.

This constitutes **protocol insolvency**: the protocol's liabilities (rsETH at inflated price) exceed its true ETH-denominated assets.

---

### Likelihood Explanation

frxETH has historically maintained its peg but is a synthetic asset with no hard redemption guarantee at 1:1 ETH. Any significant frxETH depeg event (e.g., Frax protocol stress, liquidity crisis) immediately activates this vulnerability. The structural flaw is always present; it only requires a market condition that has precedent for similar pegged assets (stETH depegged to ~0.94 ETH in June 2022). The entry path requires no special permissions — any user can call `updateRSETHPrice()` and then deposit sfrxETH. [5](#0-4) 

---

### Recommendation

Replace `pricePerShare()` with a two-step calculation that accounts for frxETH's own ETH value:

```
sfrxETH/ETH = sfrxETH.pricePerShare() * frxETH/ETH
```

where `frxETH/ETH` is sourced from a Chainlink `frxETH/ETH` feed or a Curve pool TWAP. Alternatively, use a Chainlink `sfrxETH/ETH` feed directly if one becomes available, bypassing frxETH entirely.

---

### Proof of Concept

1. frxETH depegs to 0.95 ETH (market event, no admin action required).
2. Protocol holds 1000 sfrxETH. `pricePerShare()` = 1.05 frxETH/sfrxETH.
3. `SfrxETHPriceOracle.getAssetPrice(sfrxETH)` returns `1.05e18` (treated as ETH).
4. True ETH value = 1000 × 1.05 × 0.95 = 997.5 ETH. Oracle-reported value = 1000 × 1.05 = 1050 ETH. Overstatement = 52.5 ETH.
5. Attacker calls `updateRSETHPrice()` — public, no access control — to bake the inflated price into `rsETHPrice`.
6. Attacker deposits 100 sfrxETH (true ETH value = 99.75 ETH) and receives rsETH priced at the inflated rate.
7. Attacker initiates withdrawal for ETH/stETH, receiving ~105 ETH worth of assets.
8. Net extraction: ~5.25 ETH per 100 sfrxETH deposited, funded by diluting existing rsETH holders. [2](#0-1) [6](#0-5)

### Citations

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L8-11)
```text
interface ISfrxETH {
    /// @notice How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD
    function pricePerShare() external view returns (uint256);
}
```

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L35-41)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != sfrxETHContractAddress) {
            revert InvalidAsset();
        }

        return ISfrxETH(sfrxETHContractAddress).pricePerShare();
    }
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

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```
