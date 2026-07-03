### Title
Missing Update Function for `wrsETH` Config Field Across All L2 Pool Contracts - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
Every configurable state variable in the L2 pool contracts has a dedicated setter function except `wrsETH` (the wrapped rsETH token address). This field is set once at initialization and cannot be updated without a full UUPS contract upgrade, unlike every other config field.

### Finding Description
In `RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolV2.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV2NBA.sol`, and `RSETHPoolNoWrapper.sol`, the `wrsETH` (or `rsETH` in `RSETHPoolNoWrapper`) address is set once in `initialize()` and never again.

All other configurable fields have dedicated setters:

| Field | Setter |
|---|---|
| `feeBps` | `setFeeBps()` |
| `rsETHOracle` | `setRSETHOracle()` |
| `isEthDepositEnabled` | `setIsEthDepositEnabled()` |
| `dailyMintLimit` | `setDailyMintLimit()` |
| `l1VaultETHForL2Chain` | `setL1VaultETHForL2Chain()` |
| `stargatePool` | `setStargatePool()` |
| `dstLzChainId` | `setDstLzChainId()` |
| `l2Bridge` | `setL2Bridge()` |
| `messenger` | `setMessenger()` |
| `supportedTokenOracle` | `setSupportedTokenOracle()` |
| `tokenBridge` | `setTokenBridge()` |
| **`wrsETH`** | **None** |

`wrsETH` is set in `initialize()`: [1](#0-0) 

It is used in every deposit path to mint tokens to users: [2](#0-1) 

And in the reverse-swap path as the wrapper reference: [3](#0-2) 

The same pattern holds in `RSETHPoolV3ExternalBridge`: [4](#0-3) 

And in `RSETHPoolNoWrapper`, where `rsETH` (the canonical OFT) is set in `initialize()` with no setter: [5](#0-4) 

### Impact Explanation
If the `wrsETH` wrapper contract needs to be replaced — for example, due to a security issue in the wrapper, a protocol migration, or a wrapper upgrade — the pool admin cannot update the `wrsETH` address via a simple privileged call. A full UUPS proxy upgrade is required instead. During the upgrade window, deposits must be paused, preventing users from swapping ETH/LSTs for rsETH. The contract fails to deliver its core promised service (minting wrsETH) until the upgrade completes.

**Impact**: Low — Contract fails to deliver promised returns, but doesn't lose value. Existing user holdings of `wrsETH` are unaffected; only new deposits are blocked during the upgrade.

### Likelihood Explanation
Wrapper contract replacements are uncommon but plausible in a live protocol (e.g., wrapper bug, OFT migration, LayerZero V2 upgrade). The missing setter is a structural gap that affects every deployed pool variant simultaneously.

### Recommendation
Add a privileged setter for `wrsETH` in each affected pool contract, consistent with the pattern used for all other config fields:

```solidity
event WrsETHSet(address wrsETH);

function setWrsETH(address _wrsETH) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_wrsETH);
    wrsETH = IERC20WrsETH(_wrsETH);
    emit WrsETHSet(_wrsETH);
}
```

Apply the equivalent fix to `RSETHPoolNoWrapper.setRsETH()`.

### Proof of Concept
1. Protocol deploys `RSETHPoolV3` with `wrsETH = WrapperV1`.
2. A critical bug is discovered in `WrapperV1`; protocol deploys `WrapperV2`.
3. Admin attempts to update `wrsETH` to `WrapperV2` — no setter exists.
4. Admin must pause deposits and execute a full UUPS upgrade to change the single address.
5. During the upgrade window, all user deposits revert (`deposit()` calls `wrsETH.mint()` which targets the old/broken wrapper).
6. Compare: `rsETHOracle` can be swapped in one transaction via `setRSETHOracle()`; `wrsETH` cannot. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L207-232)
```text
    function initialize(
        address admin,
        address bridger,
        address _wrsETH,
        uint256 _feeBps,
        address _rsETHOracle,
        bool _isEthDepositEnabled
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_wrsETH);
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        __ERC20_init("rsETH", "rsETH");
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
        isEthDepositEnabled = _isEthDepositEnabled;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolV3.sol (L426-431)
```text
        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));
        IERC20 tokenContract = IERC20(token);

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();
```

**File:** contracts/pools/RSETHPoolV3.sol (L516-537)
```text
    /// @dev Sets the fee basis points
    /// @param _feeBps The fee basis points
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }

    /// @dev Sets the isEthDepositEnabled flag
    /// @param _isEthDepositEnabled The isEthDepositEnabled flag
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(TIMELOCK_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
    }

    /// @dev Sets the rsETHOracle address
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L349-351)
```text
        wrsETH = IERC20WrsETH(_wrsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L213-216)
```text
        rsETH = IERC20(_rsETH);
        feeBps = _feeBps;
        rsETHOracle = _rsETHOracle;
        isEthDepositEnabled = _isEthDepositEnabled;
```
