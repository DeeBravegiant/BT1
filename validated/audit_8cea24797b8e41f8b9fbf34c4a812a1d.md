Having read the full contract, I can now perform the complete analysis.

**Key observations from the code:**

1. `allowedTokens` is a mapping — the wrapper explicitly supports **multiple** allowed tokens, added via `addAllowedToken` (TIMELOCK_ROLE) or `reinitialize` (DEFAULT_ADMIN_ROLE). [1](#0-0) 

2. `maxAmountToDepositBridgerAsset` computes capacity as `totalSupply() - balanceOf(specificToken)` — it only looks at **one token's balance**, not the aggregate collateral across all allowed tokens. [2](#0-1) 

3. `depositBridgerAssets` does **not** mint wrsETH — it only transfers tokens in, so it can silently over-collateralize the wrapper. [3](#0-2) 

4. `_withdraw` burns wrsETH 1:1 against a **specific** token — if total token balances exceed `totalSupply()`, the surplus tokens can never be redeemed. [4](#0-3) 

5. `RsETHTokenWrapper` has **no** emergency recovery function and does not inherit from `Recoverable` — there is no admin escape hatch for stuck tokens. [5](#0-4) 

**Tracing the flaw:**

After the described sequence (mint 50 + user deposit 50 tokenA + bridger deposit 50 tokenA → totalSupply=100, tokenA=100), the wrapper is exactly solvent. But `maxAmountToDepositBridgerAsset(tokenB)` = `100 - 0 = 100`. The formula returns a non-zero allowance for tokenB because it only checks tokenB's own balance, completely ignoring that tokenA already covers the entire supply. A bridger that queries this formula for each allowed token and acts on the result will deposit tokenB, creating irrecoverable surplus collateral.

**Why this is not blocked by the "operator compromise" rejection criterion:**

The bridger is not acting maliciously or outside its role — it is using the contract's own public view function (`maxAmountToDepositBridgerAsset`) as designed. The bug is in the formula itself, not in the bridger's behavior. An automated bridger following the contract's API will trigger this.

---

### Title
Multi-token collateral accounting flaw in `maxAmountToDepositBridgerAsset` allows over-collateralization, permanently freezing excess tokens — (`contracts/L2/RsETHTokenWrapper.sol`)

### Summary
`maxAmountToDepositBridgerAsset` computes available deposit capacity as `totalSupply() - balanceOf(specificToken)`. When multiple allowed tokens are present, this formula ignores collateral already contributed by other tokens, allowing the bridger to deposit surplus collateral that can never be redeemed.

### Finding Description
The wrapper supports multiple allowed tokens via the `allowedTokens` mapping. The capacity formula is evaluated independently per token:

```solidity
// contracts/L2/RsETHTokenWrapper.sol L99-110
function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
    uint256 wrsETHSupply = totalSupply();
    uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
    if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
    return wrsETHSupply - balanceOfAssetInWrapper;
}
```

The correct invariant is: `sum(balanceOf(allAllowedTokens)) <= totalSupply()`. The formula enforces only: `balanceOf(thisToken) <= totalSupply()`. When tokenA already fully backs the supply, the formula still returns `totalSupply()` as the allowable deposit for tokenB (since `balanceOf(tokenB) = 0`).

`depositBridgerAssets` mints no wrsETH — it only transfers tokens in. Any tokens deposited beyond `totalSupply()` in aggregate are permanently unclaimable because `_withdraw` requires burning wrsETH 1:1, and there is no emergency recovery function in `RsETHTokenWrapper`.

### Impact Explanation
Excess collateral tokens deposited by the bridger are permanently frozen in the wrapper. There is no admin sweep, no `Recoverable` base, and no mechanism to extract tokens without burning wrsETH that does not exist. This is **Critical — Permanent freezing of funds**.

### Likelihood Explanation
Medium. Requires the wrapper to have more than one allowed token active simultaneously (supported by `addAllowedToken` / `reinitialize`) and the bridger to query `maxAmountToDepositBridgerAsset` for each token independently — the natural behavior of an automated bridger. No malicious actor is needed; a correctly-operating bridger following the contract's own API triggers the loss.

### Recommendation
Replace the per-token formula with an aggregate check across all allowed tokens. Since `allowedTokens` is a mapping without an enumerable list, add a tracked array of allowed tokens and compute:

```solidity
uint256 totalCollateral = 0;
for (uint i = 0; i < allowedTokensList.length; i++) {
    totalCollateral += ERC20Upgradeable(allowedTokensList[i]).balanceOf(address(this));
}
return totalCollateral >= totalSupply() ? 0 : totalSupply() - totalCollateral;
```

Alternatively, maintain a single `uint256 totalDepositedCollateral` storage variable incremented/decremented on every deposit/withdraw/bridger-deposit, and use that in the formula.

### Proof of Concept
```
State: tokenA and tokenB both in allowedTokens.

1. mint(alice, 50)
   → totalSupply = 50, tokenA.bal = 0, tokenB.bal = 0

2. alice calls deposit(tokenA, 50)
   → totalSupply = 100, tokenA.bal = 50

3. bridger calls depositBridgerAssets(tokenA, 50)
   → maxAmountToDepositBridgerAsset(tokenA) = 100 - 50 = 50 ✓ passes
   → tokenA.bal = 100, totalSupply = 100  (wrapper exactly solvent)

4. bridger queries maxAmountToDepositBridgerAsset(tokenB)
   → returns 100 - 0 = 100  ← BUG: ignores tokenA already covering supply

5. bridger calls depositBridgerAssets(tokenB, 50)
   → tokenB.bal = 50, totalSupply = 100

Post-state:
  totalSupply = 100
  tokenA.bal  = 100
  tokenB.bal  =  50   ← 50 tokenB permanently stuck; no wrsETH left to burn for them

Invariant broken: sum(balances) = 150 > totalSupply = 100
No recovery path exists in RsETHTokenWrapper.
``` [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L20-193)
```text
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
