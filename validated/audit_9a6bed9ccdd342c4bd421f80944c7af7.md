The code confirms all factual claims in the report. Let me verify the key elements:

**`i_maxSupply` is immutable with no setter:** [1](#0-0) [2](#0-1) 

**`mint()` enforces the hard cap:** [3](#0-2) 

**No function in the contract modifies `i_maxSupply`** — the only read accessor is `maxSupply()` at line 72-74, which is view-only. There is no setter anywhere in the file. [4](#0-3) 

**CCIP bridge flow on L1 commits rsETH before L2 delivery:** [5](#0-4) 

All code references are accurate. The exploit path is valid: the contract explicitly supports finite-cap deployment (the constructor accepts any `maxSupply_` without restriction, and the comment at line 19 confirms this is an intended configuration). Once `totalSupply()` reaches `i_maxSupply` through organic bridging, every subsequent CCIP `mint()` call reverts permanently with no admin recovery path. The impact — permanent freezing of unclaimed yield — matches an allowed impact class.

---

Audit Report

## Title
Immutable `i_maxSupply` Permanently Blocks CCIP Mint Delivery When Supply Cap Is Reached - (File: `contracts/ccip/WrappedRSETH.sol`)

## Summary
`WrappedRSETH` accepts a `maxSupply_` parameter at construction that is stored as `immutable i_maxSupply` with no setter. When `totalSupply()` reaches this cap, `mint()` reverts with `MaxSupplyExceeded`. Because `i_maxSupply` cannot be raised after deployment, any in-flight CCIP message targeting `mint()` will fail on every retry, permanently stranding the bridged tokens for affected recipients.

## Finding Description
`i_maxSupply` is declared `uint256 internal immutable` at line 41 and assigned once in the constructor at line 54. The `mint()` function at line 138 enforces: `if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(...)`. No function in the contract modifies `i_maxSupply`. The CCIP token pool on L2 calls `WrappedRSETH.mint(recipient, amount)` to complete message delivery. Once `totalSupply() == i_maxSupply`, every such call reverts. CCIP manual execution retries will also revert for the same reason. The L1 rsETH is already locked in the CCIP token pool (`L1VaultV2.bridgeRsETHToL2UsingCCIP` at lines 360–364 commits the tokens before the message is sent), so there is no L1-side recovery either.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Once the cap is reached, bridged yield tokens cannot be delivered to L2 recipients. The L1 rsETH is committed to the bridge and the L2 mint is permanently blocked. This matches the allowed impact class exactly.

## Likelihood Explanation
The precondition is deployment with `maxSupply_ > 0`, which the constructor accepts without restriction and the contract comment explicitly documents as a supported mode. No attacker action is required; organic bridging activity alone fills the supply. The condition is reachable in any production deployment that uses a finite cap, making likelihood medium.

## Recommendation
1. Deploy with `maxSupply_ = 0` if `WrappedRSETH` is intended as an unbounded CCIP bridge token.
2. If a cap is required, replace `immutable i_maxSupply` with a mutable state variable and add an owner-restricted setter:
   ```solidity
   uint256 internal s_maxSupply;
   function setMaxSupply(uint256 newMax) external onlyOwner { s_maxSupply = newMax; }
   ```
3. Add a CCIP message recovery path that escrows pending mints on `MaxSupplyExceeded` so they can be retried after the cap is raised.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;
import "forge-std/Test.sol";
import "contracts/ccip/WrappedRSETH.sol";

contract MaxSupplyPoC is Test {
    WrappedRSETH token;
    address owner  = address(0xA);
    address minter = address(0xB); // simulates CCIP token pool
    address user   = address(0xC);
    address yieldR = address(0xD);

    function setUp() public {
        vm.prank(owner);
        token = new WrappedRSETH("Wrapped rsETH", "wrsETH", 18, 1000e18, owner);
        vm.prank(owner);
        token.grantMintRole(minter);
    }

    function test_maxSupplyBlocksCCIPDelivery() public {
        vm.prank(minter);
        token.mint(user, 1000e18); // fills supply to cap

        vm.prank(minter);
        vm.expectRevert(
            abi.encodeWithSelector(WrappedRSETH.MaxSupplyExceeded.selector, 1000e18 + 1)
        );
        token.mint(yieldR, 1); // permanently blocked — no recovery path
    }
}
```
Expected: `mint(yieldR, 1)` reverts with `MaxSupplyExceeded(1000000000000000000001)`. `token.maxSupply()` returns `1000e18` and cannot be changed.

### Citations

**File:** contracts/ccip/WrappedRSETH.sol (L41-41)
```text
    uint256 internal immutable i_maxSupply;
```

**File:** contracts/ccip/WrappedRSETH.sol (L53-54)
```text
        i_decimals = decimals_;
        i_maxSupply = maxSupply_;
```

**File:** contracts/ccip/WrappedRSETH.sol (L72-74)
```text
    function maxSupply() public view virtual returns (uint256) {
        return i_maxSupply;
    }
```

**File:** contracts/ccip/WrappedRSETH.sol (L137-141)
```text
    function mint(address account, uint256 amount) external override onlyMinter validAddress(account) {
        if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(totalSupply() + amount);

        _mint(account, amount);
    }
```

**File:** contracts/L1VaultV2.sol (L360-366)
```text
        IERC20(address(rsETH)).safeIncreaseAllowance(address(ccipRouter), amount);

        Client.EVM2AnyMessage memory message = getCCIPMessage(amount);

        bytes32 messageId = ccipRouter.ccipSend{ value: msg.value }(destinationChainSelector, message);

        emit BridgedRsETHToL2UsingCCIP(destinationChainSelector, l2Receiver, amount, messageId);
```
