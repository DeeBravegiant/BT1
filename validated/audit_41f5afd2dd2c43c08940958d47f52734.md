### Title
`swapAssetToPremintedRsETH()` Bypasses Pause Check Contrary to `deposit()` - (File: `contracts/pools/RSETHPoolV2ExternalBridge.sol`)

### Summary
Multiple L2 pool contracts enforce `whenNotPaused` on all `deposit()` functions but omit the same guard from `swapAssetToPremintedRsETH()`, which also pulls tokens from callers and disburses pool assets. In `RSETHPoolV2ExternalBridge.sol` this function is reachable by `WHITELISTED_USER_ROLE` holders — a role distinct from `OPERATOR_ROLE` that can be granted to external parties — allowing them to interact with the pool and drain its ETH reserves even when the contract is paused.

### Finding Description
Every `deposit()` entry point in the affected pool contracts carries `whenNotPaused`:

- `RSETHPoolV2ExternalBridge.deposit()` [1](#0-0) 
- `RSETHPoolV3.deposit(string)` and `deposit(address,uint256,string)` [2](#0-1) 
- `RSETHPoolV3ExternalBridge.deposit(string)` and `deposit(address,uint256,string)` [3](#0-2) 
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `deposit(address,uint256,string)` [4](#0-3) 

`swapAssetToPremintedRsETH()` in every one of these contracts is missing `whenNotPaused`:

- `RSETHPoolV2ExternalBridge.swapAssetToPremintedRsETH()` — guarded only by `onlyOperatorOrWhitelisted(msg.sender)`, no pause check: [5](#0-4) 
- `RSETHPoolV3.swapAssetToPremintedRsETH()` — guarded only by `onlyRole(OPERATOR_ROLE)`, no pause check: [6](#0-5) 
- `RSETHPoolV3ExternalBridge.swapAssetToPremintedRsETH()` — guarded only by `onlyRole(OPERATOR_ROLE)`, no pause check: [7](#0-6) 
- `RSETHPoolV3WithNativeChainBridge.swapAssetToPremintedRsETH()` — guarded only by `onlyRole(OPERATOR_ROLE)`, no pause check: [8](#0-7) 

The critical case is `RSETHPoolV2ExternalBridge`. Its access modifier is `onlyOperatorOrWhitelisted`, which passes for either `OPERATOR_ROLE` **or** `WHITELISTED_USER_ROLE`:

```solidity
modifier onlyOperatorOrWhitelisted(address account) {
    if (!hasRole(OPERATOR_ROLE, account) && !hasRole(WHITELISTED_USER_ROLE, account)) {
        revert NotOperatorOrWhitelisted();
    }
    _;
}
``` [9](#0-8) 

`WHITELISTED_USER_ROLE` is a separate, named role (`keccak256("WHITELISTED_USER_ROLE")`) that can be granted to external integration partners or market makers by the admin. [10](#0-9) 

Inside `swapAssetToPremintedRsETH()`, the function pulls rsETH from the caller and sends ETH out of the pool — a token-taking operation identical in nature to `deposit()`, yet without the pause guard: [11](#0-10) 

The `paused` flag and `whenNotPaused` modifier are defined in the contract: <cite repo="Tylerpinwa/LRT-rsETH--010" path="contracts/pools/RSETHPoolV2ExternalBridge.sol"

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L90-91)
```text
    bytes32 public constant WHITELISTED_USER_ROLE = keccak256("WHITELISTED_USER_ROLE");

```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L130-135)
```text
    modifier onlyOperatorOrWhitelisted(address account) {
        if (!hasRole(OPERATOR_ROLE, account) && !hasRole(WHITELISTED_USER_ROLE, account)) {
            revert NotOperatorOrWhitelisted();
        }
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L418-425)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlyOperatorOrWhitelisted(msg.sender)
    {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L437-443)
```text
        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the ETH from the pool to the sender
        if (getETHBalanceMinusFees() < ethAmount) revert InsufficientETHBalanceForReverseSwap();
        (bool success,) = payable(msg.sender).call{ value: ethAmount }("");
        if (!success) revert TransferFailed();
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-251)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV3.sol (L414-423)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-371)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L578-587)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-287)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L448-457)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
```
