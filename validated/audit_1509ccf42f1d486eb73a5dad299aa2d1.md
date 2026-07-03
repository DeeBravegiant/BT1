Audit Report

## Title
Stale ETH Snapshot in `LRTConverter.ethValueInWithdrawal` Causes rsETH Price Mis-accounting — (File: contracts/LRTConverter.sol)

## Summary
`LRTConverter.transferAssetFromDepositPool` records the ETH value of incoming LSTs using the oracle price at transfer time, storing it in `ethValueInWithdrawal`. Because `getAssetDistributionData` hard-codes `assetLyingInConverter = 0` for every LST, the converter's holdings are exclusively represented by this stale snapshot via `getETHDistributionData → ethLyingInConverter`. Any oracle price movement between the transfer and the next `updateRSETHPrice()` call causes the protocol to mis-value those assets, producing an incorrect rsETH price.

## Finding Description

**Step 1 — Snapshot recorded at transfer time.**
`transferAssetFromDepositPool` stamps `ethValueInWithdrawal` with the oracle price at the moment of transfer:

```solidity
// LRTConverter.sol L140-142
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
``` [1](#0-0) 

**Step 2 — LST balance in converter is zeroed out.**
`getAssetDistributionData` explicitly sets `assetLyingInConverter = 0` for every LST, with a comment acknowledging the design:

```solidity
// LRTDepositPool.sol L460
assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
``` [2](#0-1) 

**Step 3 — Only the stale snapshot is used.**
`getETHDistributionData` reads the frozen snapshot rather than computing a live value:

```solidity
// LRTDepositPool.sol L498-499
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [3](#0-2) 

**Step 4 — rsETH price is derived from this stale figure.**
`_getTotalEthInProtocol` iterates all supported assets. For `ETH_TOKEN`, `getTotalAssetDeposits` routes through `getETHDistributionData`, pulling in the stale `ethValueInWithdrawal` multiplied by `getAssetPrice(ETH_TOKEN) = 1e18`. For the LST itself, the converter contribution is 0. The result is that the converter's LST holdings are valued at the old price, not the current oracle price. [4](#0-3) 

**Why existing checks are insufficient.**
`transferAssetToDepositPool` and `_sendEthToDepositPool` both adjust `ethValueInWithdrawal` using the oracle price at the time of *those* calls, not the original transfer price. This means the snapshot can drift in either direction throughout the converter's holding period with no correction mechanism until the assets leave the converter. [5](#0-4) 

## Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

- If the LST oracle price **rises** after transfer: `ethValueInWithdrawal` underestimates the converter's holdings → `_getTotalEthInProtocol` is too low → rsETH price is understated → new depositors receive more rsETH than warranted, diluting existing holders.
- If the LST oracle price **falls** after transfer: the reverse holds — rsETH price is overstated, new depositors receive fewer rsETH than warranted.

The magnitude is bounded by the oracle price drift during the converter's holding period multiplied by the volume of assets in transit. No funds are directly stolen, but the rsETH price fails to accurately reflect the protocol's true ETH backing.

## Likelihood Explanation

**Low-to-Medium.** `transferAssetFromDepositPool` is a routine operational call made as part of normal LST-to-ETH conversion. LST oracle prices (e.g., stETH) drift continuously with Lido staking rewards. The mis-accounting persists for the entire duration between the transfer and the eventual `claimStEth` / `_sendEthToDepositPool` call, which can span multiple days. The public `updateRSETHPrice()` function can be called by any unprivileged user at any time during this window, materialising the stale price into the stored `rsETHPrice`. [6](#0-5) 

## Recommendation

Replace the static ETH snapshot with a live per-asset accounting approach. Track raw asset amounts deposited into the converter per asset (e.g., `mapping(address => uint256) public assetAmountInConverter`), and compute their current ETH value on-the-fly inside `getETHDistributionData` by iterating over those balances and multiplying by the current oracle price — mirroring how `getAssetDistributionData` values LSTs held in the deposit pool and NDCs. This eliminates the staleness window entirely.

## Proof of Concept

1. Deposit pool holds 10,000 stETH; stETH oracle price = 1.00 ETH.
2. Operator calls `transferAssetFromDepositPool(stETH, 10_000e18)`.
   - `ethValueInWithdrawal` is set to `10_000e18 * 1e18 / 1e18 = 10_000e18`.
   - stETH balance in deposit pool drops to 0; `assetLyingInConverter` is always 0.
3. Lido distributes rewards; stETH oracle price rises to 1.05 ETH.
4. Any user calls `updateRSETHPrice()`.
   - `_getTotalEthInProtocol` iterates supported assets:
     - stETH contribution: `0 * 1.05 = 0` (none in pool/NDCs, converter zeroed out).
     - ETH_TOKEN contribution includes `ethLyingInConverter = 10_000e18` (stale snapshot).
   - Actual ETH value of the 10,000 stETH in the converter = `10,000 * 1.05 = 10,500 ETH`.
   - Protocol under-reports 500 ETH of value.
5. rsETH price is computed lower than it should be; new depositors are minted excess rsETH at the expense of existing holders.

**Foundry fork test plan:**
- Fork mainnet; deploy/configure the protocol contracts.
- Deposit stETH into the pool; call `transferAssetFromDepositPool`.
- Warp forward and simulate a Lido rebase (or mock the oracle to return a higher price).
- Call `updateRSETHPrice()` and assert that `rsETHPrice` is lower than `totalETH / rsETHSupply` computed using the live oracle price for the converter's stETH balance.

### Citations

**File:** contracts/LRTConverter.sol (L140-142)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
```

**File:** contracts/LRTConverter.sol (L252-263)
```text
    function _sendEthToDepositPool(uint256 _amount) internal {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
        emit EthTransferred(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L460-461)
```text
        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
