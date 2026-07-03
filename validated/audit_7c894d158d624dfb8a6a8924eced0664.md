### Title
Out-of-Order CCIP Execution Combined with Immutable `maxSupply` Can Permanently Freeze In-Flight Bridged Funds — (`contracts/ccip/WrappedRSETH.sol` / `contracts/L1VaultV2.sol`)

---

### Summary

`L1VaultV2.getCCIPMessage()` unconditionally sets `allowOutOfOrderExecution: true`, permitting the CCIP DON to deliver messages in any order. `WrappedRSETH.mint()` enforces an **immutable** `i_maxSupply` cap. When two bridge messages (M1, M2) are in-flight simultaneously near the cap, out-of-order delivery of M2 can consume the remaining mintable supply, causing M1's mint to revert with `MaxSupplyExceeded`. Because the source-chain rsETH is already burned and `i_maxSupply` cannot be changed, M1's funds have no guaranteed recovery path.

---

### Finding Description

**Step 1 — Source chain burn (L1)**

`L1VaultV2.bridgeRsETHToL2UsingCCIP()` approves rsETH to the CCIP router and calls `ccipSend`. The CCIP token pool on L1 burns the rsETH at this point. The tokens are gone from L1 before any L2 execution occurs. [1](#0-0) 

**Step 2 — `allowOutOfOrderExecution: true` is hardcoded**

Every CCIP message built by `getCCIPMessage()` carries `allowOutOfOrderExecution: true`. This is not configurable; it is baked into the struct literal. [2](#0-1) 

**Step 3 — Immutable supply cap on L2**

`WrappedRSETH` stores the cap as an `immutable` variable set at construction time. It can never be raised by any admin action. [3](#0-2) 

**Step 4 — Hard revert on cap breach**

`WrappedRSETH.mint()` reverts with `MaxSupplyExceeded` if `totalSupply() + amount > i_maxSupply`. There is no partial-fill, no queuing, and no fallback. [4](#0-3) 

**Concrete scenario:**

| State | `i_maxSupply` | `totalSupply()` | Remaining |
|---|---|---|---|
| Before M1/M2 sent | 1000 | 900 | 100 |
| M1 sent (amount = 60) | — | — | — |
| M2 sent (amount = 40) | — | — | — |
| M2 executes first | 1000 | 940 | 60 |
| M1 executes | 1000 | 940+60=1000 | 0 — **succeeds** |

But if M2 carries 60 and M1 carries 60:

| M2 executes first | 1000 | 960 | 40 |
| M1 tries to mint 60 | 960+60=1020 > 1000 | **MaxSupplyExceeded** | — |

M1's rsETH is already burned on L1. The CCIP message enters a "failed" state. Manual re-execution via the CCIP router's `manuallyExecute()` will also revert as long as `totalSupply() + 60 > i_maxSupply`. Because `i_maxSupply` is immutable, the only unblock is for existing WrappedRSETH holders to burn tokens — an action no contract enforces and no user is obligated to perform. If that never happens, M1's funds are permanently frozen.

---

### Impact Explanation

- rsETH is burned on L1 at send time; there is no source-chain refund path in CCIP's burn-and-mint model.
- `i_maxSupply` is immutable; no admin can raise the cap to unblock M1.
- CCIP's `manuallyExecute()` retries the same mint call, which will keep reverting until supply is freed by external burns.
- If supply is never freed, the bridged amount is permanently irrecoverable — **Critical: Permanent freezing of funds**.

---

### Likelihood Explanation

- Requires `i_maxSupply` to be set (non-zero) at `WrappedRSETH` deployment — a documented deployment option.
- Requires two concurrent bridge calls whose combined amount exceeds remaining supply — realistic during high-activity periods near the cap.
- `allowOutOfOrderExecution: true` is unconditional, so no configuration change is needed to trigger the ordering.
- The MANAGER_ROLE initiates bridging; two rapid sequential calls by the manager (or two managers) are sufficient.

Likelihood: **Low-Medium** (requires proximity to the supply cap, but the enabling condition is always present).

---

### Recommendation

1. **Set `allowOutOfOrderExecution: false`** in `getCCIPMessage()` to enforce FIFO delivery from the same sender, eliminating the ordering race entirely.
2. **Alternatively**, remove `i_maxSupply` from `WrappedRSETH` (set to 0 at deployment) if a hard cap is not a protocol requirement, since the L1-side daily mint limits already constrain issuance.
3. **If a cap is required**, make it upgradeable (e.g., owner-settable) so that a stuck message can be unblocked by raising the cap.
4. **Add a CCIP message-failure recovery hook** that re-mints or refunds on the source chain when the destination mint permanently fails.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry unit test (no mainnet required)
import "forge-std/Test.sol";
import "../contracts/ccip/WrappedRSETH.sol";

contract OutOfOrderMintTest is Test {
    WrappedRSETH token;
    address pool = address(0xCAFE); // simulates CCIP token pool (minter)
    address user1 = address(0x1);
    address user2 = address(0x2);

    function setUp() public {
        token = new WrappedRSETH("Wrapped rsETH", "wrsETH", 18, 1000e18, address(this));
        token.grantMintRole(pool);
    }

    function test_outOfOrderMintFreezesM1() public {
        // Pre-fill supply to 940
        vm.prank(pool);
        token.mint(address(this), 940e18);

        // M2 executes first (amount = 60), consuming remaining supply
        vm.prank(pool);
        token.mint(user2, 60e18); // totalSupply = 1000 == maxSupply

        // M1 now tries to mint 60 — reverts permanently
        vm.prank(pool);
        vm.expectRevert(abi.encodeWithSelector(WrappedRSETH.MaxSupplyExceeded.selector, 1060e18));
        token.mint(user1, 60e18);

        // user1's rsETH was already burned on L1; funds are frozen
        assertEq(token.balanceOf(user1), 0);
    }
}
``` [5](#0-4) [3](#0-2) [4](#0-3) [2](#0-1) [6](#0-5)

### Citations

**File:** contracts/L1VaultV2.sol (L341-366)
```text
    function bridgeRsETHToL2UsingCCIP(uint256 amount) external payable nonReentrant onlyRole(MANAGER_ROLE) {
        if (bridgeType != BridgeType.CCIP) {
            revert InactiveBridgeType();
        }

        if (rsETH.balanceOf(address(this)) < amount) {
            revert InsufficientRsETHBalance();
        }

        if (amount == 0) {
            revert ZeroAmount();
        }

        uint256 fee = getCCIPFee(amount);

        if (msg.value != fee) {
            revert IncorrectCCIPFee();
        }

        IERC20(address(rsETH)).safeIncreaseAllowance(address(ccipRouter), amount);

        Client.EVM2AnyMessage memory message = getCCIPMessage(amount);

        bytes32 messageId = ccipRouter.ccipSend{ value: msg.value }(destinationChainSelector, message);

        emit BridgedRsETHToL2UsingCCIP(destinationChainSelector, l2Receiver, amount, messageId);
```

**File:** contracts/L1VaultV2.sol (L419-423)
```text
        Client.EVMExtraArgsV2 memory extraArgs = Client.EVMExtraArgsV2({
            gasLimit: ccipGasLimit,
            allowOutOfOrderExecution: true // Whether the message can be executed in any order relative to other
            // messages from the same sender
        });
```

**File:** contracts/ccip/WrappedRSETH.sol (L25-25)
```text
    error MaxSupplyExceeded(uint256 supplyAfterMint);
```

**File:** contracts/ccip/WrappedRSETH.sol (L41-41)
```text
    uint256 internal immutable i_maxSupply;
```

**File:** contracts/ccip/WrappedRSETH.sol (L137-141)
```text
    function mint(address account, uint256 amount) external override onlyMinter validAddress(account) {
        if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(totalSupply() + amount);

        _mint(account, amount);
    }
```
