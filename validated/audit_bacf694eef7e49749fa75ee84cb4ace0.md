### Title
Single-Step `depositPool` Address Update Can Permanently Freeze MEV/Execution-Layer Rewards - (File: contracts/FeeReceiver.sol)

### Summary
`FeeReceiver.sol` updates the `depositPool` address in a single step with no two-step confirmation. If the `MANAGER` role sets an incorrect or inaccessible address, all ETH (MEV and execution-layer rewards) accumulated in `FeeReceiver` is permanently frozen because `sendFunds()` — the sole forwarding mechanism — will revert on every call, with no recovery path.

### Finding Description
`FeeReceiver` accumulates ETH from MEV and execution-layer rewards and forwards them to `LRTDepositPool` via `sendFunds()`. The `depositPool` address is updated by the `MANAGER` role through `setDepositPool`:

```solidity
// contracts/FeeReceiver.sol L66-L72
function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
    if (_depositPool == address(0)) revert InvalidEmptyValue();
    depositPool = _depositPool;
    emit DepositPoolSet(_depositPool);
}
```

The only validation is a zero-address check. There is no two-step confirmation requiring the new address to accept the role. The forwarding function is:

```solidity
// contracts/FeeReceiver.sol L53-L58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

`sendFunds()` has no access control and is the **only** mechanism to move ETH out of `FeeReceiver`. If `depositPool` is set to any address that does not implement `receiveFromRewardReceiver` (e.g., an EOA, a wrong contract address due to a typo, or a contract being migrated), every call to `sendFunds()` will revert. There is no emergency withdrawal, no admin rescue function, and no alternative forwarding path in `FeeReceiver.sol`. The contract is upgradeable, but recovery requires a full governance upgrade cycle. [1](#0-0) [2](#0-1) 

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

All ETH accumulated in `FeeReceiver` from MEV and execution-layer rewards becomes permanently inaccessible. `sendFunds()` is the sole exit path; with a broken `depositPool` pointer it reverts unconditionally. The rewards are not lost from the chain, but they are locked in the contract with no built-in recovery mechanism, constituting permanent freezing of unclaimed yield. [3](#0-2) 

### Likelihood Explanation
**Medium.** The `MANAGER` role is a privileged but operationally active role (it also calls `updateAssetDepositLimit`, etc.). During protocol migrations or upgrades, the manager must update `depositPool` to point to a new deployment. A single-character typo or a copy-paste of a wrong address is a realistic human error. Because the update is immediate and irreversible without a governance upgrade, the error window is permanent once the transaction is confirmed. The external report explicitly identifies this same class of mistake ("unintentionally or intentionally make a typo") as the primary risk driver. [2](#0-1) 

### Recommendation
Implement a two-step update for `depositPool`:

1. `MANAGER` calls `proposeDepositPool(address _pendingDepositPool)` — stores the candidate address in a `pendingDepositPool` state variable.
2. The candidate address calls `acceptDepositPool()` — confirms it is a live, reachable contract and atomically sets `depositPool = pendingDepositPool`.

This mirrors the pattern already present in `contracts/ccip/ConfirmedOwnerWithProposal.sol` (`transferOwnership` / `acceptOwnership`) and eliminates the risk of an irreversible misconfiguration. [4](#0-3) 

### Proof of Concept

1. `FeeReceiver` has accumulated a significant ETH balance from MEV rewards.
2. The `MANAGER` calls `setDepositPool(typoAddress)` — a non-zero address that does not implement `receiveFromRewardReceiver` (e.g., an EOA or a wrong contract).
3. `depositPool` is immediately and irrevocably set to `typoAddress`.
4. Any caller (permissionless) invokes `sendFunds()`.
5. `ILRTDepositPool(typoAddress).receiveFromRewardReceiver{value: balance}()` reverts — either because the target has no code, or because the function selector is absent.
6. Every subsequent call to `sendFunds()` reverts identically.
7. All ETH in `FeeReceiver` is permanently frozen with no built-in recovery path. [5](#0-4)

### Citations

**File:** contracts/FeeReceiver.sol (L13-72)
```text
contract FeeReceiver is IFeeReceiver, Initializable, AccessControlUpgradeable {
    address public _legacyProtocolTreasury;
    address public depositPool;
    uint256 public _legacyProtocolFeePercentInBPS;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address _protocolTreasury,
        address _depositPool,
        uint256 _protocolFeePercentInBPS,
        address admin,
        address manager
    )
        external
        initializer
    {
        if (
            _protocolTreasury == address(0) || _depositPool == address(0) || _protocolFeePercentInBPS == 0
                || admin == address(0) || manager == address(0)
        ) {
            revert InvalidEmptyValue();
        }

        _legacyProtocolTreasury = _protocolTreasury;
        depositPool = _depositPool;
        _legacyProtocolFeePercentInBPS = _protocolFeePercentInBPS;

        __AccessControl_init();
        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(LRTConstants.MANAGER, manager);
    }

    /// @dev fallback to receive funds
    receive() external payable { }

    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }

    /*//////////////////////////////////////////////////////////////
                            MANAGER FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /// @dev Set the deposit pool
    /// @param _depositPool Address of the deposit pool
    function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
        if (_depositPool == address(0)) revert InvalidEmptyValue();

        depositPool = _depositPool;

        emit DepositPoolSet(_depositPool);
    }
```

**File:** contracts/ccip/ConfirmedOwnerWithProposal.sol (L36-51)
```text
    function transferOwnership(address to) public override onlyOwner {
        _transferOwnership(to);
    }

    /**
     * @notice Allows an ownership transfer to be completed by the recipient.
     */
    function acceptOwnership() external override {
        require(msg.sender == s_pendingOwner, "Must be proposed owner");

        address oldOwner = s_owner;
        s_owner = msg.sender;
        s_pendingOwner = address(0);

        emit OwnershipTransferred(oldOwner, msg.sender);
    }
```
