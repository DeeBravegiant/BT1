### Title
Donation Attack on `LRTDepositPool` Inflates `rsETHPrice`, Enabling Theft from Early Depositors - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle._updateRsETHPrice()` computes the rsETH price from `totalETHInProtocol`, which is derived from the raw token balance of `LRTDepositPool`. Because the deposit pool accepts direct ETH via `receive()` and ERC20 tokens via direct transfer, an attacker can inflate `totalETHInProtocol` without minting rsETH. When the public `updateRSETHPrice()` is called afterward, the inflated denominator causes subsequent depositors to receive far fewer rsETH tokens than they should, while the attacker—who holds the only existing rsETH—profits.

### Finding Description

`LRTOracle._getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(asset)`, which in turn calls `getAssetDistributionData()`. That function sets:

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

and for ETH:

```solidity
ethLyingInDepositPool = address(this).balance;
```

Because `LRTDepositPool` has an open `receive()` function and accepts arbitrary ERC20 transfers, any actor can increase these balances without going through `depositAsset` or `depositETH`, and therefore without minting any rsETH.

The rsETH price is then computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

The guard against large price jumps is:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

`pricePercentageLimit` is **not initialised** in `initialize()`, so it defaults to `0`. When it is `0`, the condition is always `false` and the guard is entirely bypassed, allowing an arbitrarily large single-block price increase.

The minting formula in `LRTDepositPool.getRsETHAmountToMint()` is:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A sufficiently inflated `rsETHPrice` causes this to round down to 1 wei (or even 0 if `minRSETHAmountExpected == 0` is accepted by `_beforeDeposit`).

### Impact Explanation

**Critical / High — Direct theft of depositor funds.**

An attacker who is the sole (or dominant) rsETH holder can extract value from every subsequent depositor:

| Step | rsethSupply | totalETHInProtocol | rsETHPrice | Attacker net |
|---|---|---|---|---|
| Attacker deposits 1 wei ETH | 1 wei | 1 wei | 1e18 | −1 wei |
| Attacker donates 1 000 ETH to pool | 1 wei | 1 000e18 + 1 | — | −1 000 ETH |
| `updateRSETHPrice()` called | 1 wei | 1 000e18 + 1 | ≈1 000e18 | — |
| Victim deposits 1 999 ETH (minExpected=0) | 2 wei | 2 999e18 + 1 | — | — |
| `updateRSETHPrice()` called | 2 wei | 2 999e18 + 1 | ≈1 499.5e18 | — |
| Attacker redeems 1 wei rsETH | 1 wei | — | — | ≈+1 499.5 ETH |

**Attacker profit ≈ 499 ETH; victim loss ≈ 499 ETH** from a 1 999 ETH deposit.

### Likelihood Explanation

- `pricePercentageLimit` is `0` by default (not set in `initialize`), so the price-cap guard is disabled unless an admin explicitly calls `setPricePercentageLimit`.
- `updateRSETHPrice()` is a public, permissionless function callable by anyone.
- `LRTDepositPool.receive()` is open, enabling costless ETH