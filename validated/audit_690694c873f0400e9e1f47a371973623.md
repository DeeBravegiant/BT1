Audit Report

## Title
Unguarded Payable Receiver Functions Allow Arbitrary ETH Inflation of `totalETHInProtocol`, Causing Unearned Protocol-Fee rsETH Minting to Treasury and Diluting Existing Holder Yield - (`contracts/LRTDepositPool.sol`)

## Summary
`receiveFromRewardReceiver()`, `receiveFromNodeDelegator()`, and `receiveFromLRTConverter()` in `LRTDepositPool` are `external payable` with no access control, allowing any caller to inflate `address(this).balance`. Because `getETHDistributionData()` returns `address(this).balance` verbatim as `ethLyingInDepositPool`, and `LRTOracle._updateRsETHPrice()` uses this value to compute `totalETHInProtocol`, the oracle treats the donated ETH as legitimate staking yield, computes a `protocolFeeInETH` on it, and mints excess rsETH to the treasury — permanently diluting every existing rsETH holder's yield share.

## Finding Description

**Step 1 — No access control on receiver functions.** [1](#0-0) 
All three functions are bare `external payable` stubs with no role modifier, no `whenNotPaused`, and no `msg.sender` validation. Any EOA or contract can call them with arbitrary ETH.

**Step 2 — Inflated balance flows directly into ETH TVL.** [2](#0-1) 
`getETHDistributionData()` assigns `address(this).balance` to `ethLyingInDepositPool` with no mechanism to distinguish legitimate reward/node-delegator transfers from arbitrary donations.

**Step 3 — Oracle aggregates the inflated balance into `totalETHInProtocol`.** [3](#0-2) 
`_getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(ETH_TOKEN)`, which calls `getAssetDistributionData(ETH_TOKEN)`, which delegates to `getETHDistributionData()` — returning the inflated balance.

**Step 4 — Unearned fee is computed and minted.** [4](#0-3) 
`rewardAmount = totalETHInProtocol − previousTVL` is inflated by the donated ETH. `protocolFeeInETH = rewardAmount × protocolFeeInBPS / 10_000` is therefore larger than it should be. [5](#0-4) 
The excess fee is minted as rsETH to the treasury, permanently diluting every existing holder's share of future yield.

**Why existing guards are insufficient:**
- `pricePercentageLimit` can revert if the donation is too large relative to TVL, but the attacker can calibrate the donation to stay within the threshold or split it across multiple blocks/transactions.
- `maxFeeMintAmountPerDay` caps per-day damage but does not prevent the attack; it can be repeated each day.
- `updateRSETHPrice()` is itself public (`whenNotPaused` only), so the attacker can trigger the oracle update in the same transaction.

## Impact Explanation
**High — Theft of unclaimed yield.** Every existing rsETH holder is entitled to their proportional share of legitimate staking yield. When the treasury is minted rsETH it did not earn, the total rsETH supply increases without a corresponding increase in backing, permanently reducing each existing holder's share of all future yield distributions. The attacker loses the donated ETH (it becomes protocol backing), but the treasury captures `protocolFeeInBPS / 10_000` of the donation as an unearned fee — a concrete, irreversible transfer of yield from existing holders to the treasury. This is not purely griefing: the treasury is the direct financial beneficiary of each attack.

## Likelihood Explanation
- The three receiver functions are publicly callable with zero preconditions; no role, no deposit, no prior state required.
- `updateRSETHPrice()` is also public; the attacker can atomically donate and trigger the oracle update in one transaction.
- The attacker loses the donated ETH, so the realistic actor is a party economically motivated to harm existing holders (e.g., a short position on rsETH) or to benefit the treasury (e.g., a treasury-aligned actor).
- The `pricePercentageLimit` guard is bypassable by calibrating donation size or splitting across blocks.
- `maxFeeMintAmountPerDay` limits per-day damage but the attack is fully repeatable each day.

## Recommendation
Add caller validation to all three receiver functions and the bare `receive()` fallback:

```solidity
function receiveFromRewardReceiver() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_REWARD_RECEIVER))
        revert CallerNotRewardReceiver();
}

function receiveFromNodeDelegator() external payable {
    if (isNodeDelegator[msg.sender] == 0) revert CallerNotNodeDelegator();
}

function receiveFromLRTConverter() external payable {
    if (msg.sender != lrtConfig.getContract(LRTConstants.LRT_CONVERTER))
        revert CallerNotLRTConverter();
}

receive() external payable {
    revert DirectETHTransferNotAllowed();
}
```

This ensures only the named legitimate contracts can increase `address(this).balance` through these entry points, making any inflation of `ethLyingInDepositPool` attributable to genuine protocol flows.

## Proof of Concept
Minimal Foundry fork test sequence:

1. Fork mainnet/testnet with protocol deployed; record `rseth.totalSupply()`, `oracle.rsETHPrice()`, and `rseth.balanceOf(treasury)`.
2. Compute `previousTVL = totalSupply * rsETHPrice / 1e18`.
3. `vm.deal(attacker, 100 ether); vm.prank(attacker); depositPool.receiveFromRewardReceiver{value: 100 ether}();`
4. `oracle.updateRSETHPrice();` (public, no role required).
5. Assert `rseth.balanceOf(treasury) > treasuryBefore` — treasury received rsETH despite zero legitimate staking yield occurring.
6. Assert `rseth.balanceOf(treasury) - treasuryBefore ≈ (100 ether * protocolFeeInBPS / 10_000) / newRsETHPrice` — the excess matches the unearned fee formula exactly.
7. Repeat steps 3–6 on the next day to confirm repeatability up to `maxFeeMintAmountPerDay`.

### Citations

**File:** contracts/LRTDepositPool.sol (L61-67)
```text
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
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
