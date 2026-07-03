Audit Report

## Title
L1Vault Balances Excluded from TVL Accounting Enables rsETH Price Manipulation and Share Dilution - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

## Summary
`getETHDistributionData()` and `getAssetDistributionData()` in `LRTDepositPool.sol` enumerate every protocol location holding assets except `L1Vault` contracts. Because `updateRSETHPrice()` is a public, permissionless function, an attacker can trigger a price update while L1Vault holds undeposited bridged assets, causing `rsETHPrice` to be written below its true value. The attacker then deposits at the deflated price, receiving excess rsETH that dilutes all existing holders once the manager restores the true TVL.

## Finding Description

`LRTOracle._getTotalEthInProtocol()` computes total backing ETH by iterating over supported assets and calling `ILRTDepositPool.getTotalAssetDeposits(asset)` for each.

`getTotalAssetDeposits` delegates to `getAssetDistributionData`, which for ETH calls `getETHDistributionData()`. That function accounts for:
- `LRTDepositPool` ETH balance
- Each NDC's ETH balance
- EigenLayer pod shares
- Unstaking ETH
- `LRTUnstakingVault` balance
- `LRTConverter.ethValueInWithdrawal()`

For LSTs, `getAssetDistributionData` accounts for:
- `LRTDepositPool` token balance
- Each NDC's token balance
- EigenLayer strategy shares
- Unstaking LST amounts
- `LRTUnstakingVault` token balance

`L1Vault` is absent from both lists. `L1Vault` accepts ETH directly from the L2 bridge via its `receive()` function and holds ETH/LSTs until a manager manually calls `depositETHForL1VaultETH()` or `depositAssetForL1Vault()`. During this window, those assets are invisible to `_getTotalEthInProtocol()`.

`updateRSETHPrice()` is declared `public whenNotPaused` — no role restriction. The price formula is:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
```

With L1Vault assets excluded, `totalETHInProtocol` is understated, so `newRsETHPrice` is set below its true value. The minting formula in `getRsETHAmountToMint` is:

```
rsethAmountToMint = (amount * assetPrice) / rsETHPrice
```

A lower `rsETHPrice` denominator yields more rsETH per unit deposited.

The downside-protection check at lines 270–282 of `LRTOracle.sol` only triggers a pause if `pricePercentageLimit > 0` AND the price drop exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`. Since `pricePercentageLimit` defaults to `0`, the condition `pricePercentageLimit > 0` is false and the protocol never pauses on a price decrease unless the admin has explicitly configured this value. Even when configured, an attacker can exploit L1Vault balances small enough to stay within the threshold.

## Impact Explanation

**High — Theft of unclaimed yield.**

When the attacker deposits at the artificially low `rsETHPrice`, they receive excess rsETH shares. Once the manager calls `depositETHForL1VaultETH()` or `depositAssetForL1Vault()`, the true TVL is restored and `rsETHPrice` rises to its correct level. The attacker's excess shares now represent a claim on assets they did not contribute, directly extracting value from all existing rsETH holders. The profit scales with both the attacker's deposit size and the magnitude of the L1Vault balance relative to total TVL.

## Likelihood Explanation

**Medium.** L1Vault holding undeposited assets is a routine operational state — assets arrive from L2 bridges continuously and are processed by the manager in batches. The protocol operates across multiple L2 chains (Arbitrum, Optimism, Scroll, Blast, Mode, etc.), each with its own `L1Vault`, so the aggregate unaccounted balance can be material. The attacker only needs to monitor L1Vault balances on-chain and call the public `updateRSETHPrice()` at the right moment before the manager acts. No special privileges are required.

## Recommendation

1. **Include L1Vault balances in accounting:** Maintain a registry of all deployed `L1Vault` addresses in `LRTConfig` and sum their ETH and LST balances inside `getETHDistributionData()` and `getAssetDistributionData()` respectively.
2. **Alternatively, restrict `updateRSETHPrice()`:** Require a privileged role (manager/operator) to call `updateRSETHPrice()`, removing the permissionless attack vector entirely.
3. **Ensure `pricePercentageLimit` is configured:** Set a non-zero `pricePercentageLimit` as a defense-in-depth measure to limit the magnitude of any single price manipulation.

## Proof of Concept

1. 500 ETH arrives at `L1Vault` from the Arbitrum bridge via `receive()`. Manager has not yet called `depositETHForL1VaultETH()`.
2. Attacker calls `LRTOracle.updateRSETHPrice()`. `_getTotalEthInProtocol()` sums all tracked locations but misses the 500 ETH in `L1Vault`. Suppose true TVL = 10,000 ETH, rsETH supply = 9,500; true price ≈ 1.0526 ETH/rsETH. With 500 ETH missing, computed price ≈ 1.0000 ETH/rsETH. `rsETHPrice` is written as ~1.0000. (If `pricePercentageLimit` is 0, no pause is triggered.)
3. Attacker immediately calls `LRTDepositPool.depositETH()` with 100 ETH. `rsethAmountToMint = 100e18 * 1e18 / 1.0000e18 = 100 rsETH` instead of the correct ~95 rsETH.
4. Manager calls `depositETHForL1VaultETH()`, depositing 500 ETH into the pool. Next `updateRSETHPrice()` call restores price to ~1.0526.
5. Attacker holds 100 rsETH worth ~105.26 ETH, having deposited only 100 ETH — a ~5.26 ETH gain extracted from existing holders.

**Foundry fork test outline:**
```solidity
function testL1VaultPriceManipulation() public {
    // Fork mainnet, set up protocol state with known TVL
    // Send 500 ETH to L1Vault simulating bridge arrival
    vm.deal(address(l1Vault), 500 ether);
    // Record attacker rsETH balance before
    uint256 priceBefore = lrtOracle.rsETHPrice();
    // Attacker triggers price update
    lrtOracle.updateRSETHPrice();
    uint256 priceAfter = lrtOracle.rsETHPrice();
    assertLt(priceAfter, priceBefore); // price is understated
    // Attacker deposits 100 ETH
    uint256 rsethMinted = depositPool.getRsETHAmountToMint(ETH, 100 ether);
    // Manager processes L1Vault
    vm.prank(manager);
    l1Vault.depositETHForL1VaultETH();
    lrtOracle.updateRSETHPrice();
    uint256 priceRestored = lrtOracle.rsETHPrice();
    // Attacker's rsETH is now worth more than 100 ETH
    uint256 attackerValue = rsethMinted * priceRestored / 1e18;
    assertGt(attackerValue, 100 ether);
}
```