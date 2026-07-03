### Title
`minAmountToDeposit` Defaults to Zero with No Lower Bound, Allowing Dust Deposits That Yield Zero rsETH Due to Rounding - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool` initializes `minAmountToDeposit` to zero by default and `setMinAmountToDeposit()` imposes no lower bound. When this threshold is zero (the default state), a depositor can supply a dust amount of an LST and, due to integer-division truncation in `getRsETHAmountToMint()`, receive zero rsETH while their assets are permanently transferred into the protocol.

---

### Finding Description

**Root cause 1 — zero default, no lower bound on setter.**

`minAmountToDeposit` is declared as a storage variable but never assigned in `initialize()`, so it starts at `0`: [1](#0-0) 

The admin setter accepts any value including zero: [2](#0-1) 

**Root cause 2 — dust deposit passes the guard.**

`_beforeDeposit` only rejects `depositAmount == 0` or amounts strictly below `minAmountToDeposit`. When `minAmountToDeposit == 0`, every non-zero deposit passes: [3](#0-2) 

**Root cause 3 — integer division truncates to zero.**

`getRsETHAmountToMint` computes:

```
rsethAmountToMint = (amount * getAssetPrice(asset)) / rsETHPrice()
``` [4](#0-3) 

As rsETH appreciates, `rsETHPrice()` grows above `1e18`. For a dust deposit (e.g., 1 wei of stETH where `getAssetPrice ≈ 1e18` and `rsETHPrice = 1.1e18`):

```
(1 × 1e18) / 1.1e18 = 0   (truncated)
```

**Root cause 4 — no zero-rsETH guard before minting.**

`_beforeDeposit` only checks `rsethAmountToMint < minRSETHAmountExpected`. When the caller passes `minRSETHAmountExpected = 0`, a zero result passes silently: [5](#0-4) 

`depositAsset` then transfers the caller's tokens in and calls `_mintRsETH(0)`, minting nothing: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A depositor who supplies a dust amount of an LST with `minRSETHAmountExpected = 0` has their tokens permanently transferred to the protocol while receiving zero rsETH. The deposited assets inflate the protocol's TVL, which raises `rsETHPrice` on the next `updateRSETHPrice()` call, silently redistributing value to all existing rsETH holders at the expense of the dust depositor. This matches the **Low** impact tier: *"Contract fails to deliver promised returns, but doesn't lose value"* — the protocol does not lose value; the depositor does.

---

### Likelihood Explanation

`minAmountToDeposit` is zero from deployment (never set in `initialize`). Any caller who passes `minRSETHAmountExpected = 0` — a common default in integrations or scripts — and deposits a sufficiently small amount triggers the issue without any admin action. Likelihood is **Low** because the practical loss per transaction is dust-sized, but the condition is always present in the default deployment state.

---

### Recommendation

1. **Initialize `minAmountToDeposit` to a safe non-zero value** inside `initialize()` (e.g., `0.001 ether`).
2. **Add a lower bound in `setMinAmountToDeposit()`** — reject values below a protocol-defined minimum, analogous to how `KernelVaultETH.setMinDeposit()` rejects zero: [8](#0-7) 
3. **Add an explicit zero-rsETH guard in `_beforeDeposit()`**:
   ```solidity
   if (rsethAmountToMint == 0) revert InvalidAmountToDeposit();
   ```

---

### Proof of Concept

```
State: minAmountToDeposit = 0 (default, never initialized)
       rsETHPrice = 1.05e18 (5% appreciation after launch)
       stETH assetPrice = 1e18

1. Attacker/user calls:
   depositAsset(stETH, 1 wei, minRSETHAmountExpected=0, "")

2. _beforeDeposit check:
   depositAmount (1) == 0? No.
   depositAmount (1) < minAmountToDeposit (0)? No. → passes

3. getRsETHAmountToMint(stETH, 1):
   = (1 * 1e18) / 1.05e18 = 0  (integer truncation)

4. rsethAmountToMint (0) < minRSETHAmountExpected (0)? No. → passes

5. safeTransferFrom(user, depositPool, 1 wei stETH) → user loses 1 wei stETH
6. _mintRsETH(0) → user receives 0 rsETH

Result: 1 wei stETH permanently absorbed by protocol; rsETH supply unchanged;
        next rsETHPrice update benefits all existing holders.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L30-51)
```text
    uint256 public minAmountToDeposit;

    mapping(address => uint256) public isNodeDelegator; // 0: not a node delegator, 1: is a node delegator
    address[] public nodeDelegatorQueue;

    /// @notice maximum amount that can be ignored
    uint256 public maxNegligibleAmount;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Initializes the contract
    /// @param lrtConfigAddr LRT config address
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
```

**File:** contracts/LRTDepositPool.sol (L113-116)
```text
        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L282-285)
```text
    function setMinAmountToDeposit(uint256 minAmountToDeposit_) external onlyLRTAdmin {
        minAmountToDeposit = minAmountToDeposit_;
        emit MinAmountToDepositUpdated(minAmountToDeposit_);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L305-308)
```text
    function setMinDeposit(uint256 _minDeposit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_minDeposit == 0) {
            revert InvalidMinDeposit();
        }
```
