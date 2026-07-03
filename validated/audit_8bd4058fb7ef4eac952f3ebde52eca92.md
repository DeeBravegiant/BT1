### Title
Missing Zero-Address Validation for `safeAddress` in `KERNEL` Token Constructor - (File: `contracts/KERNEL/KERNEL.sol`)

### Summary
The `KERNEL` token constructor accepts a `safeAddress` parameter and immediately mints the entire fixed supply of 1,000,000,000 KERNEL tokens to it, with no zero-address guard. If `address(0)` is supplied at deployment, the entire token supply is permanently burned to the zero address with no recovery path.

### Finding Description
In `contracts/KERNEL/KERNEL.sol`, the constructor is:

```solidity
constructor(address safeAddress) ERC20("KERNEL", "KERNEL") ERC20Permit("KERNEL") {
    _mint(safeAddress, 1_000_000_000 * 10 ** decimals());
}
``` [1](#0-0) 

There is no call to `UtilLib.checkNonZeroAddress(safeAddress)` before the mint. This is inconsistent with every other contract in the codebase that accepts address parameters — for example, `KernelVaultETH.initialize`, `KernelDepositPool.initialize`, `KernelMerkleDistributor.initialize`, and `KernelReceiver.initialize` all call `UtilLib.checkNonZeroAddress` on every address argument before use. [2](#0-1) 

The `KERNEL` contract has no `mint` function beyond the constructor, so the supply is fixed at deployment. There is no admin function, upgrade path, or re-initialization mechanism that could recover tokens sent to `address(0)`. [3](#0-2) 

### Impact Explanation
If `safeAddress = address(0)` is passed at deployment, all 1,000,000,000 KERNEL tokens are minted to the zero address and are permanently irrecoverable. The KERNEL token protocol is insolvent from inception: no user, staker, or depositor can ever receive KERNEL tokens, the `KernelDepositPool` staking rewards system is rendered non-functional, and the `KernelMerkleDistributor` claim system has nothing to distribute. This maps to **Critical — permanent freezing of funds / protocol insolvency**.

### Likelihood Explanation
Low. The deployer must pass `address(0)` as `safeAddress`. However, the absence of any guard means the contract provides zero protection against this deployment mistake, and since the contract is not upgradeable and has no secondary mint path, the error is entirely unrecoverable once the transaction is confirmed.

### Recommendation
Add a zero-address check at the top of the constructor, consistent with the pattern used throughout the rest of the codebase:

```solidity
constructor(address safeAddress) ERC20("KERNEL", "KERNEL") ERC20Permit("KERNEL") {
    UtilLib.checkNonZeroAddress(safeAddress);
    _mint(safeAddress, 1_000_000_000 * 10 ** decimals());
}
```

### Proof of Concept
1. Deploy `KERNEL` with `safeAddress = address(0)`.
2. Observe that `balanceOf(address(0)) == 1_000_000_000 * 1e18` and `totalSupply() == 1_000_000_000 * 1e18`.
3. No address other than `address(0)` holds any KERNEL tokens.
4. Attempt to call `KernelDepositPool.stake(...)` — the staking token balance of any real user is zero; the protocol is permanently non-functional.
5. There is no `mint`, `upgrade`, or `reinitialize` function in `KERNEL.sol` to recover from this state. [1](#0-0)

### Citations

**File:** contracts/KERNEL/KERNEL.sol (L9-11)
```text
    constructor(address safeAddress) ERC20("KERNEL", "KERNEL") ERC20Permit("KERNEL") {
        _mint(safeAddress, 1_000_000_000 * 10 ** decimals());
    }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L156-160)
```text
        UtilLib.checkNonZeroAddress(_admin);
        UtilLib.checkNonZeroAddress(_operator);
        UtilLib.checkNonZeroAddress(_kernel);
        UtilLib.checkNonZeroAddress(_kernelOftAdapter);
        UtilLib.checkNonZeroAddress(_receiver);
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
