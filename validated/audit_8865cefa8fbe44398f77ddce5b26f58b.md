Audit Report

## Title
Unguarded `receive()` in `LRTUnstakingVault` Enables Any Caller to Trigger Protocol Fee Minting on Donated ETH, Stealing Yield from rsETH Holders — (`contracts/LRTUnstakingVault.sol`)

## Summary

`LRTUnstakingVault.receive()` accepts ETH from any address with no access control. Because `LRTDepositPool.getETHDistributionData()` reads the vault's ETH balance directly via `lrtUnstakingVault.balance`, an attacker can inflate the reported TVL by donating ETH. When `LRTOracle.updateRSETHPrice()` is subsequently called (permissionless), the inflated TVL is treated as staking yield, causing the protocol to mint rsETH to the treasury as a protocol fee — permanently diluting the yield owed to existing rsETH holders.

## Finding Description

**Root cause — unguarded `receive()` in `LRTUnstakingVault`:**

`LRTUnstakingVault.receive()` (L81–83) emits an event and accepts any ETH with no role check, whitelist, or guard:

```solidity
receive() external payable {
    emit EthReceived(msg.sender, msg.value);
}
```

**TVL accounting reads raw `.balance`:**

`LRTDepositPool.getETHDistributionData()` (L495–496) reads the vault's ETH balance directly from the EVM:

```solidity
address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
ethLyingInUnstakingVault = lrtUnstakingVault.balance;
```

This value flows into `getTotalAssetDeposits(ETH)` (L385–397) as `assetLyingUnstakingVault`, which is summed into the total and returned to `LRTOracle._getTotalEthInProtocol()` (L341–343).

**Permissionless price update uses inflated TVL:**

`LRTOracle.updateRSETHPrice()` (L87–89) is public, gated only by `whenNotPaused`. Inside `_updateRsETHPrice()`, `previousTVL` is computed as `rsethSupply × rsETHPrice` (L234). Any ETH donated to the vault causes `totalETHInProtocol > previousTVL`, triggering the fee branch (L244–247):

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

**Fee is minted to treasury:**

The computed `protocolFeeInETH` is converted to rsETH and minted to the treasury (L299–307):

```solidity
uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
if (rsethAmountToMintAsProtocolFee > 0) {
    address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
    IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
```

**Why existing guards are insufficient:**

- `pricePercentageLimit`: Can be bypassed by keeping each donation small enough to stay within the threshold per call.
- `maxFeeMintAmountPerDay`: Caps daily fee minting but is set by the manager to allow normal protocol operation; the attacker simply repeats across multiple days.
- `_checkAndUpdateDailyFeeMintLimit`: Reverts if the daily cap is exceeded, but this only limits per-day damage, not the attack itself.

## Impact Explanation

This is **High — Theft of unclaimed yield**. Every ETH donated by the attacker to `LRTUnstakingVault` causes the protocol to levy its fee on that ETH as if it were staking yield. At a 10% protocol fee, donating 100 ETH causes 10 ETH worth of rsETH to be minted to the treasury rather than accruing proportionally to existing rsETH holders. The donated ETH is real backing (so the rsETH price does rise), but a portion of the price increase that should have accrued entirely to existing holders is instead captured as a protocol fee. The yield loss to existing holders is permanent and irreversible. The attacker loses their donated ETH but inflicts a concrete, on-chain yield loss on all rsETH holders.

## Likelihood Explanation

The attack requires only two permissionless on-chain actions: a plain ETH transfer to `LRTUnstakingVault` and a call to `LRTOracle.updateRSETHPrice()`. No privileged role, no front-running dependency, no flash loan, and no external protocol assumption is needed. The attack is repeatable across days (limited only by `maxFeeMintAmountPerDay` per day), and the `pricePercentageLimit` guard can be circumvented by splitting donations into smaller amounts. Any externally-owned account with ETH can execute this.

## Recommendation

1. **Restrict `receive()` in `LRTUnstakingVault`** to only accept ETH from known, trusted senders (e.g., `LRTDepositPool`, `NodeDelegator`, EigenLayer withdrawal contracts). Revert on unexpected senders.
2. **Replace raw `.balance` reads with an internal accounting variable** in both `LRTUnstakingVault` and `LRTDepositPool.getETHDistributionData()`. Increment this variable only via authorised `receive*` functions, and use it in place of `lrtUnstakingVault.balance` (L496) and `address(this).balance` (L480).
3. Apply the same access-control fix to `LRTDepositPool.receive()` (L58), which is also unguarded and feeds `ethLyingInDepositPool` via `address(this).balance` (L480).

## Proof of Concept

```solidity
// Foundry fork test (mainnet fork)
function testDonationInflatesProtocolFee() public {
    // Baseline state
    uint256 rsethSupplyBefore  = rsETH.totalSupply();
    uint256 treasuryBalBefore  = rsETH.balanceOf(treasury);
    uint256 priceBefore        = lrtOracle.rsETHPrice();

    // Step 1: Attacker donates 10 ETH directly to LRTUnstakingVault
    vm.deal(attacker, 10 ether);
    vm.prank(attacker);
    (bool ok,) = address(lrtUnstakingVault).call{value: 10 ether}("");
    require(ok, "ETH transfer failed");

    // Step 2: Anyone calls updateRSETHPrice() — no role required
    lrtOracle.updateRSETHPrice();

    // Step 3: Treasury received rsETH it should not have
    uint256 treasuryMinted = rsETH.balanceOf(treasury) - treasuryBalBefore;
    assertGt(treasuryMinted, 0, "treasury should have received fee rsETH");

    // Step 4: Existing holders' yield was diluted by treasuryMinted
    // The rsETH price rose, but a portion of the rise was captured as fee
    // rather than accruing entirely to pre-existing holders.
    uint256 priceAfter = lrtOracle.rsETHPrice();
    assertGt(priceAfter, priceBefore);
    // Without the fee, priceAfter would be higher by treasuryMinted * priceAfter / rsethSupplyBefore
}
```