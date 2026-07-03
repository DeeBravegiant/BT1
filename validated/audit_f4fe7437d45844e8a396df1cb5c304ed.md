### Title
`AGETHPoolV3` Lacks Pause Functionality Unlike Every Other Pool Contract - (`contracts/agETH/AGETHPoolV3.sol`)

### Summary
`AGETHPoolV3` does not inherit from `PausableUpgradeable` and implements no pause mechanism of any kind. Every other pool contract in the protocol — `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge` — includes emergency pause controls. This is a direct analog to M-04: a contract that should follow the inheritance pattern established by its sibling contracts but deviates from it, causing missing safety functionality that the protocol is expected to provide.

### Finding Description
`AGETHPoolV3` inherits from `ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable` but omits `PausableUpgradeable`. Its `deposit` functions carry no `whenNotPaused` guard and there is no `pause()`, `unpause()`, or `paused` state anywhere in the contract: [1](#0-0) 

```solidity
contract AGETHPoolV3 is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
``` [2](#0-1) 

```solidity
function deposit(string memory referralId) external payable nonReentrant {
    if (!isEthDepositEnabled) revert EthDepositDisabled();
``` [3](#0-2) 

```solidity
function deposit(address token, uint256 amount, string memory referralId)
    external
    nonReentrant
    onlySupportedToken(token)
```

By contrast, the structurally identical `RSETHPoolNoWrapper` properly inherits `PausableUpgradeable` and guards every deposit path: [4](#0-3) 

```solidity
contract RSETHPoolNoWrapper is AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
``` [5](#0-4) 

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
```

Even `RSETHPoolV3`, which uses a custom `bool public paused` instead of `PausableUpgradeable`, still applies `whenNotPaused` to all deposit paths and exposes `pause()`/`unpause()` functions: [6](#0-5) 

`AGETHPoolV3` has no equivalent protection whatsoever — no `paused` state, no `pause()`/`unpause()` functions, and no modifier on any deposit path. The `isEthDepositEnabled` flag is not a pause mechanism; it is a permanent feature toggle, not an emergency stop.

### Impact Explanation
In an emergency — oracle misconfiguration, discovered exploit, or rate manipulation — the protocol has no mechanism to halt agETH deposits. All other pool contracts can be stopped by the `PAUSER_ROLE` holder; `AGETHPoolV3` cannot. Users depositing agETH during an incident cannot be protected, and the protocol fails to deliver the emergency-stop guarantee it provides for every other pool. **Impact: Low — Contract fails to deliver promised returns.**

### Likelihood Explanation
The missing pause functionality is a permanent structural gap, not a transient condition. Any emergency affecting the agETH oracle (`agETHOracle`) or pool logic would expose depositors to unmitigated risk with no on-chain recourse. The likelihood of an emergency scenario over the protocol's lifetime is non-trivial given the complexity of cross-chain oracle feeds and the precedent set by the protocol adding pause controls to every other pool.

### Recommendation
Add `PausableUpgradeable` to `AGETHPoolV3`'s inheritance chain, call `__Pausable_init()` in `initialize`, add a `PAUSER_ROLE` constant, and apply `whenNotPaused` to both `deposit` overloads — mirroring the pattern in `RSETHPoolNoWrapper`:

```diff
-contract AGETHPoolV3 is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
+contract AGETHPoolV3 is ERC20Upgradeable, AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {

+    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

     function initialize(...) external initializer {
         __ERC20_init("agETH", "agETH");
         __AccessControl_init();
+        __Pausable_init();
         __ReentrancyGuard_init();
         ...
     }

-    function deposit(string memory referralId) external payable nonReentrant {
+    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {

-    function deposit(address token, uint256 amount, string memory referralId) external nonReentrant onlySupportedToken(token) {
+    function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused onlySupportedToken(token) {

+    function pause() external onlyRole(PAUSER_ROLE) { _pause(); }
+    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) { _unpause(); }
```

### Proof of Concept
1. Interact with the deployed `AGETHPoolV3` proxy.
2. Attempt to call `pause()` — the call reverts because no such function exists on the contract.
3. Call `deposit{value: 1 ether}("")` — it succeeds regardless of any emergency state, because there is no pause guard.
4. Compare with `RSETHPoolNoWrapper.deposit`, which reverts with `EnforcedPause` when the contract is paused by the `PAUSER_ROLE` holder.

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L24-24)
```text
contract AGETHPoolV3 is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/agETH/AGETHPoolV3.sol (L115-117)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L134-142)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        onlySupportedToken(token)
    {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L30-30)
```text
contract RSETHPoolNoWrapper is AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-231)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-252)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```
