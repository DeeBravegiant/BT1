### Title
Immutable `i_maxSupply` Cap in `WrappedRSETH.mint()` Causes Permanent Freezing of CCIP-Bridged Funds - (`contracts/ccip/WrappedRSETH.sol`)

---

### Summary

`WrappedRSETH` enforces an immutable maximum supply cap (`i_maxSupply`) set at deployment. When a CCIP inbound message attempts to mint tokens that would exceed this cap, the `mint()` call reverts with `MaxSupplyExceeded`. Because `i_maxSupply` is `immutable` and can never be raised, the CCIP message will fail on every retry attempt. The source-chain rsETH is already locked in the CCIP token pool with no protocol-level refund path, resulting in permanent freezing of the bridged amount.

---

### Finding Description

`WrappedRSETH` is the destination-chain token used in the CCIP lock-and-mint bridge flow. Its `mint()` function enforces a hard cap: [1](#0-0) 

```solidity
function mint(address account, uint256 amount) external override onlyMinter validAddress(account) {
    if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(totalSupply() + amount);
    _mint(account, amount);
}
```

`i_maxSupply` is declared `immutable` and set only in the constructor: [2](#0-1) 

There is no setter, upgrade path, or any mechanism to raise this cap post-deployment.

On the source chain, `L1VaultV2.bridgeRsETHToL2UsingCCIP()` locks rsETH by granting allowance to the CCIP router and calling `ccipSend`: [3](#0-2) 

Once `ccipSend` is called, the source-chain rsETH is locked in the CCIP token pool. The CCIP protocol then delivers a message to the destination chain, where the token pool calls `WrappedRSETH.mint()`. If that call reverts (due to `MaxSupplyExceeded`), CCIP marks the message as failed and allows manual re-execution — but since the cap is immutable, every re-execution attempt will also revert. There is no protocol-level mechanism to return the locked tokens to the sender.

---

### Impact Explanation

**Critical. Permanent freezing of funds.**

- rsETH is locked in the CCIP token pool on L1 with no recovery path.
- `WrappedRSETH` cannot be upgraded (it is not a proxy) and `i_maxSupply` cannot be changed.
- CCIP manual execution retries will always revert for the same reason.
- The bridged amount is permanently irrecoverable.

---

### Likelihood Explanation

**Medium-to-High** (conditional on deployment configuration):

- The vulnerability is latent whenever `WrappedRSETH` is deployed with a non-zero `maxSupply_`. The contract comment explicitly states: *"The total supply can be limited during deployment."*
- As the L2 wrapped supply grows organically toward the cap, any bridge transaction that would push `totalSupply + amount > i_maxSupply` triggers the freeze.
- No attacker action is required — a legitimate user bridging a normal amount near the cap boundary is sufficient.
- The CCIP bridge is a production path (`L1VaultV2.bridgeRsETHToL2UsingCCIP`) with no pre-flight check against `WrappedRSETH.maxSupply()`. [4](#0-3) 

---

### Recommendation

1. **Deploy `WrappedRSETH` with `maxSupply_ = 0`** (unlimited) if the CCIP bridge is active, since the bridge invariant requires every locked token to be mintable.
2. **Add a pre-flight guard** in `L1VaultV2.bridgeRsETHToL2UsingCCIP()` that queries `WrappedRSETH.maxSupply()` and `totalSupply()` and reverts before locking source-chain tokens if the mint would exceed the cap.
3. **Replace `immutable` with a governable setter** for `i_maxSupply` in `WrappedRSETH`, protected by an appropriate access control role, so the cap can be raised if needed.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test (local, no mainnet)
import "forge-std/Test.sol";
import "../contracts/ccip/WrappedRSETH.sol";

contract MaxSupplyFreezePoC is Test {
    WrappedRSETH token;
    address minter = address(0xBEEF);
    address user   = address(0xCAFE);

    function setUp() public {
        // Deploy with a finite cap of 1000 tokens
        token = new WrappedRSETH("Wrapped rsETH", "wrsETH", 18, 1000e18, address(this));
        token.grantMintRole(minter);
    }

    function test_permanentFreeze() public {
        // Simulate prior bridge activity: mint 990 tokens (totalSupply = 990e18)
        vm.prank(minter);
        token.mint(user, 990e18);

        // CCIP inbound message tries to mint 20 tokens (990 + 20 = 1010 > 1000)
        // This simulates the CCIP token pool calling mint() on destination chain
        vm.prank(minter);
        vm.expectRevert(abi.encodeWithSelector(WrappedRSETH.MaxSupplyExceeded.selector, 1010e18));
        token.mint(user, 20e18);

        // i_maxSupply is immutable — no way to raise it
        assertEq(token.maxSupply(), 1000e18);

        // Retry will always revert — funds locked on source chain permanently
        vm.prank(minter);
        vm.expectRevert(abi.encodeWithSelector(WrappedRSETH.MaxSupplyExceeded.selector, 1010e18));
        token.mint(user, 20e18);
    }
}
```

The test demonstrates that once `totalSupply` is near `i_maxSupply`, any CCIP mint attempt for an amount that would exceed the cap will always revert, with no on-chain recovery path. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/ccip/WrappedRSETH.sol (L19-25)
```text
/// @dev The total supply can be limited during deployment.
contract WrappedRSETH is IBurnMintERC20, ERC677, IERC165, ERC20Burnable, ConfirmedOwnerWithProposal {
    using EnumerableSet for EnumerableSet.AddressSet;

    error SenderNotMinter(address sender);
    error SenderNotBurner(address sender);
    error MaxSupplyExceeded(uint256 supplyAfterMint);
```

**File:** contracts/ccip/WrappedRSETH.sol (L41-54)
```text
    uint256 internal immutable i_maxSupply;

    constructor(
        string memory name,
        string memory symbol,
        uint8 decimals_,
        uint256 maxSupply_,
        address _owner
    )
        ERC677(name, symbol)
        ConfirmedOwnerWithProposal(_owner, address(0))
    {
        i_decimals = decimals_;
        i_maxSupply = maxSupply_;
```

**File:** contracts/ccip/WrappedRSETH.sol (L137-141)
```text
    function mint(address account, uint256 amount) external override onlyMinter validAddress(account) {
        if (i_maxSupply != 0 && totalSupply() + amount > i_maxSupply) revert MaxSupplyExceeded(totalSupply() + amount);

        _mint(account, amount);
    }
```

**File:** contracts/L1VaultV2.sol (L341-367)
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
    }
```

**File:** contracts/ccip/IBurnMintERC20.sol (L6-11)
```text
interface IBurnMintERC20 is IERC20 {
    /// @notice Mints new tokens for a given address.
    /// @param account The address to mint the new tokens to.
    /// @param amount The number of tokens to be minted.
    /// @dev this function increases the total supply.
    function mint(address account, uint256 amount) external;
```
