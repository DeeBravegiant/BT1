I have the full contract. Let me analyze the exact call sequence against the code.

The full contract is confirmed. Here is the complete analysis:

---

### Title
Bridger-deposited collateral permanently frozen after `removeAllowedToken` — (`contracts/L2/RsETHTokenWrapper.sol`)

### Summary
`depositBridgerAssets` transfers altRsETH into the wrapper **without minting wrsETH**. After `TIMELOCK_ROLE` calls `removeAllowedToken`, every withdrawal path checks `allowedTokens[_asset]` and reverts. No emergency-recovery, sweep, or rescue function exists in the contract, leaving the deposited collateral irrecoverable through any on-chain path in the current code.

### Finding Description

`depositBridgerAssets` is a collateral-only deposit: it pulls tokens from the bridger but mints nothing. [1](#0-0) 

`removeAllowedToken` simply flips the mapping to `false` with no check for existing contract balance. [2](#0-1) 

Both `_withdraw` and `_deposit` gate on `allowedTokens[_asset]`, so after removal every user-facing exit path (`withdraw`, `withdrawTo`) reverts with `TokenNotAllowed`. [3](#0-2) 

A grep for `emergencyWithdraw`, `sweep`, `rescue`, and `recoverToken` returns zero matches in the file — there is no alternative extraction path. [4](#0-3) 

### Impact Explanation
Any altRsETH deposited via `depositBridgerAssets` before the token is removed becomes permanently unrecoverable through the current contract code. The only theoretical escape is a proxy upgrade, but that is not a built-in mechanism and requires the admin to recognise the problem and act — it is not guaranteed.

**Impact: Critical — Permanent freezing of funds.**

### Likelihood Explanation
The scenario does **not** require a malicious or compromised admin. It requires two independent, individually legitimate operations:
1. The bridger collateralises already-minted wrsETH (normal operational flow).
2. TIMELOCK deprecates a bridge token (normal governance action, e.g., migrating to a new bridge).

Neither actor needs to behave maliciously; the freeze is a silent side-effect of the second action.

**Likelihood: Medium** — token deprecation is a plausible governance event; the bridger function is explicitly labelled "Legacy," suggesting token rotation is anticipated.

### Recommendation
Add a balance guard in `removeAllowedToken` that reverts if the contract still holds a non-zero balance of the token, **or** add a privileged `recoverToken(address asset, address to, uint256 amount)` function (e.g., gated on `DEFAULT_ADMIN_ROLE`) that bypasses the allowlist check so stuck collateral can always be extracted.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Pseudo-test (Foundry style)
function test_bridgerCollateralFrozenAfterRemoval() public {
    // 1. Bridger deposits N altRsETH as collateral (no wrsETH minted)
    vm.prank(bridger);
    wrapper.depositBridgerAssets(address(altRsETH), N);
    assertEq(altRsETH.balanceOf(address(wrapper)), N);

    // 2. TIMELOCK removes the token (legitimate governance action)
    vm.prank(timelock);
    wrapper.removeAllowedToken(address(altRsETH));

    // 3. Every withdrawal path reverts — funds are stuck
    vm.expectRevert(RsETHTokenWrapper.TokenNotAllowed.selector);
    wrapper.withdraw(address(altRsETH), N);

    vm.expectRevert(RsETHTokenWrapper.TokenNotAllowed.selector);
    wrapper.withdrawTo(address(altRsETH), bridger, N);

    // 4. No other function can extract the tokens
    // → altRsETH.balanceOf(address(wrapper)) == N forever
    assertEq(altRsETH.balanceOf(address(wrapper)), N);
}
```

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L1-193)
```text
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import { SafeERC20Upgradeable } from "@openzeppelin/contracts-upgradeable/token/ERC20/utils/SafeERC20Upgradeable.sol";
import { ERC20Upgradeable } from "@openzeppelin/contracts-upgradeable/token/ERC20/ERC20Upgradeable.sol";
import {
    ERC20PermitUpgradeable
} from "@openzeppelin/contracts-upgradeable/token/ERC20/extensions/ERC20PermitUpgradeable.sol";
import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

import { UtilLib } from "contracts/utils/UtilLib.sol";

/// @title RsETHTokenWrapper
/// @notice This contract is a wrapper for alternative RsETH tokens in L2 chains from a canonical rsETH token for
/// KelpDao
/// @dev it is an upgradeable ERC20 token that wraps an alternative RsETH token
/// It also uses the ERC20PermitUpgradeable extension
/// the alt rsETH tokens can be swapped 1:1 for the canonical rsETH token
contract RsETHTokenWrapper is Initializable, AccessControlUpgradeable, ERC20Upgradeable, ERC20PermitUpgradeable {
    using SafeERC20Upgradeable for ERC20Upgradeable;

    /// @dev The address of the alternative RsETH token
    mapping(address allowedToken => bool isAllowed) public allowedTokens;

    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant BRIDGER_ROLE = keccak256("BRIDGER_ROLE");
    bytes32 public constant TIMELOCK_ROLE = keccak256("TIMELOCK_ROLE");

    error TokenNotAllowed();
    error TokenAlreadyAllowed();
    error CannotDeposit();

    event Deposit(address indexed asset, address indexed sender, address indexed receiver, uint256 amount);
    event Withdraw(address indexed asset, address indexed sender, address indexed receiver, uint256 amount);
    event BridgerDeposited(address indexed asset, address indexed bridger, uint256 amount);
    event TokenAdded(address indexed asset);
    event TokenRemoved(address indexed asset);

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Reinitialize the contract
    /// @param _altRsETH An alternative RsETH token
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }

    /// @dev Initialize the contract
    /// @param admin The address of the admin
    /// @param bridger The address of the bridger
    /// @param _altRsETH An alternative RsETH token
    function initialize(address admin, address bridger, address _altRsETH) external initializer {
        __ERC20_init("rsETHWrapper", "wrsETH");
        __ERC20Permit_init("rsETHWrapper");
        __AccessControl_init();

        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        _addAllowedToken(_altRsETH);
    }

    /// @dev Deposit altRsETH for wrsETH
    /// @param asset The address of the token to deposit
    ///@param _amount The amount of tokens to deposit
    function deposit(address asset, uint256 _amount) external {
        _deposit(asset, msg.sender, _amount);
    }

    /// @dev Deposit altRsETH for wrsETH to a user
    /// @param asset The address of the token to deposit
    /// @param _to The user to send the XERC20 to
    /// @param _amount The amount of tokens to deposit
    function depositTo(address asset, address _to, uint256 _amount) external {
        _deposit(asset, _to, _amount);
    }

    /// @dev Withdraw altRseth tokens from wrsETH
    /// @param asset The address of the token to withdraw
    /// @param _amount The amount of tokens to withdraw
    function withdraw(address asset, uint256 _amount) external {
        _withdraw(asset, msg.sender, _amount);
    }

    /// @dev Withdraw altRsETH tokens from wrsETH to a user
    /// @param asset The address of the token to withdraw
    /// @param _to The user to withdraw to
    /// @param _amount The amount of tokens to withdraw
    function withdrawTo(address asset, address _to, uint256 _amount) external {
        _withdraw(asset, _to, _amount);
    }

    /// @notice Get the maximum amount of the bridged asset that can be deposited
    /// @param _asset The address of the token to deposit
    /// @return uint256
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }

    /*//////////////////////////////////////////////////////////////
                           INTERNAL FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /// @dev Withdraw altRsETH tokens from wrsETH
    /// @param _asset The address of the token to withdraw
    /// @param _to The user to withdraw to
    /// @param _amount The amount of tokens to withdraw
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }

    /// @notice Deposit tokens into the lockbox
    /// @param _asset The address of the token to deposit
    /// @param _to The address to send the XERC20 to
    /// @param _amount The amount of tokens to deposit
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }

    /// @notice Internal function to add a token to the allowed tokens list
    /// @param _asset The address of the token to add
    function _addAllowedToken(address _asset) internal {
        UtilLib.checkNonZeroAddress(_asset);
        if (allowedTokens[_asset]) revert TokenAlreadyAllowed();

        allowedTokens[_asset] = true;
        emit TokenAdded(_asset);
    }

    /*//////////////////////////////////////////////////////////////
                           RESTRICTED ACCESS FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /// @dev Legacy function - Deposit for when the rsETH is bridged by the
    /// bridger from L1 so as to collateralize already minted wrsETH on L2
    ///
    /// @param _asset The address of the token to deposit
    /// @param _amount The amount of tokens to deposit
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }

    /// @dev Add a token to the allowed tokens list
    /// @param _asset The address of the token to add
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }

    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }

    /// @dev Mint wrsETH tokens on L2
    /// @param _to The address to mint the tokens to
    /// @param _amount The amount of tokens to mint
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
}
```
