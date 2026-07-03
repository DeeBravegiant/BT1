Audit Report

## Title
Unsolicited ETH via Open `receive()` Inflates `address(this).balance`, Blocking All ETH Deposits - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool` exposes an unconditional `receive()` fallback that allows any caller to push ETH directly into the contract. `getETHDistributionData()` reports `ethLyingInDepositPool` as the raw `address(this).balance`, which feeds into `getTotalAssetDeposits` and then into the deposit-limit guard. Because the ETH branch of `_checkIfDepositAmountExceedesCurrentLimit` compares the *current* total against the limit without adding the incoming deposit amount, a single wei of unsolicited ETH is sufficient to tip the balance over the cap and cause every subsequent `depositETH` call to revert with `MaximumDepositLimitReached`.

## Finding Description

**Open receive fallback** — any unprivileged account can push ETH into the contract: [1](#0-0) 

**Raw balance used as accounting** — `getETHDistributionData` reports the deposit-pool's ETH share as the unfiltered contract balance: [2](#0-1) 

**Call chain to the limit guard** — `getTotalAssetDeposits` calls `getAssetDistributionData`, which delegates to `getETHDistributionData` for the ETH token: [3](#0-2) [4](#0-3) 

**Asymmetric limit check** — the ETH branch tests only `totalAssetDeposits > limit`, omitting the incoming `amount`, while the LST branch correctly tests `totalAssetDeposits + amount > limit`. This means any balance inflation — even 1 wei — that pushes the stored total above the cap blocks all future deposits: [5](#0-4) 

**Guard invoked on every deposit** — `_beforeDeposit` reverts unconditionally when the check returns `true`: [6](#0-5) 

No existing check prevents unsolicited ETH from entering via `receive()`, and no mechanism discounts it from the accounting.

## Impact Explanation

All ETH deposits via `depositETH` are blocked for as long as `address(this).balance` exceeds the configured deposit limit. Users cannot deposit ETH and receive rsETH. The attacker can re-send 1 wei after each operator remediation (transferring ETH to a NodeDelegator), sustaining the denial of service indefinitely at negligible cost. This constitutes **temporary freezing of funds — Medium severity**.

## Likelihood Explanation

The `receive()` fallback is unconditionally open to any EOA or contract. The attack is cheapest (1 wei) when the protocol is operating near its ETH deposit cap, which is a normal steady-state condition. No special permissions, front-running, or external protocol compromise is required. Any externally reachable account can execute this at any time.

## Recommendation

Replace `address(this).balance` in `getETHDistributionData` with an internal accounting variable (e.g., `ethReceivedFromDepositors`) that is incremented only through the controlled `depositETH` path and decremented when ETH is transferred out to NodeDelegators. This eliminates the influence of unsolicited ETH on the deposit-limit accounting. As a secondary fix, mirror the LST branch and add the incoming `depositAmount` to `totalAssetDeposits` before comparing against the limit in `_checkIfDepositAmountExceedesCurrentLimit`, so that the ETH branch is at least consistent with the LST branch.

## Proof of Concept

1. Protocol ETH deposit limit is set to `1_000 ether`; current `getTotalAssetDeposits(ETH_TOKEN)` returns `999.9999 ether`.
2. Attacker executes: `(bool ok,) = address(lrtDepositPool).call{value: 1}("")` — costs 1 wei + gas.
3. `address(lrtDepositPool).balance` increases by 1 wei; `getETHDistributionData()` now returns `ethLyingInDepositPool = 999.9999 ether + 1 wei`.
4. `getTotalAssetDeposits(ETH_TOKEN)` now exceeds `1_000 ether`.
5. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, any_amount)` returns `true`.
6. Every call to `depositETH(...)` reverts with `MaximumDepositLimitReached`.
7. Operator remediates by calling `transferETHToNodeDelegator`, reducing `address(this).balance`.
8. Attacker repeats step 2 to immediately re-trigger the DoS.

**Foundry test sketch:**
```solidity
function test_dustDoS() public {
    // fill pool to near cap
    vm.deal(user, 999.9999 ether);
    vm.prank(user);
    lrtDepositPool.depositETH{value: 999.9999 ether}(0, "");

    // attacker sends 1 wei via receive()
    vm.deal(attacker, 1);
    vm.prank(attacker);
    (bool ok,) = address(lrtDepositPool).call{value: 1}("");
    assertTrue(ok);

    // any subsequent deposit reverts
    vm.deal(user2, 1 ether);
    vm.prank(user2);
    vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

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

**File:** contracts/LRTDepositPool.sol (L440-442)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return getETHDistributionData();
        }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-681)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```
