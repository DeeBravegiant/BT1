### Title
Daily Mint Limit Safety Mechanism Present in All Pool Variants Except `RSETHPool.sol` - (File: contracts/pools/RSETHPool.sol)

### Summary

The `limitDailyMint` circuit-breaker modifier was introduced as a security upgrade across every active L2 pool contract in the protocol, but was never applied to `RSETHPool.sol` (the Arbitrum pool). Any depositor can call `deposit(string)` or `deposit(address, uint256, string)` on `RSETHPool.sol` and drain the pool's entire rsETH balance in a single transaction, with no per-day rate cap.

### Finding Description

The protocol introduced a `limitDailyMint` modifier as a safety mechanism to cap the amount of rsETH that can be distributed per day. This was applied to:

- `RSETHPoolV2ExternalBridge.sol` — `limitDailyMint(msg.value)` on `deposit(string)` [1](#0-0) 
- `RSETHPoolV3ExternalBridge.sol` — `limitDailyMint(msg.value, ETH_IDENTIFIER)` and `limitDailyMint(amount, token)` on both deposit overloads [2](#0-1) 
- `RSETHPoolV3.sol` — same two-argument form on both deposit overloads [3](#0-2) 
- `RSETHPoolV3WithNativeChainBridge.sol` — same two-argument form on both deposit overloads [4](#0-3) 

`RSETHPool.sol` (Arbitrum) exposes the identical public `deposit(string)` and `deposit(address, uint256, string)` entry points but neither carries the `limitDailyMint` modifier:

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    if (!isEthDepositEnabled) revert EthDepositDisabled();
    ...
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
``` [5](#0-4) 

```solidity
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
{
    ...
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
``` [6](#0-5) 

`RSETHPool.sol` also has no `dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, or `startTimestamp` state variables at all, confirming the mechanism was never ported to this contract. [7](#0-6) 

The version history comment in `RSETHPoolV3ExternalBridge.sol` explicitly records that the daily mint limit was added as a deliberate security upgrade (`reinitializer(4)`), confirming it is a security control, not a cosmetic feature: [8](#0-7) 

### Impact Explanation

Without the daily cap, a depositor can call `deposit` on `RSETHPool.sol` with an arbitrarily large ETH or token amount in a single transaction and receive the pool's entire pre-loaded rsETH (LZ_RSETH on Arbitrum) balance. This:

1. **Temporarily freezes funds** for all other depositors — the pool's rsETH reserve is exhausted and no further swaps can succeed until the pool is replenished.
2. **Amplifies any oracle-lag window** — if the rsETH/ETH rate is momentarily stale (e.g., between oracle updates), an attacker can extract the full pool balance at the stale rate in one atomic call, whereas the daily limit on other pools would cap the damage to one day's quota.

Impact: **Medium — Temporary freezing of funds / theft of unclaimed yield** (pool rsETH reserve drained without rate-limiting).

### Likelihood Explanation

The entry path is fully permissionless: any EOA or contract can call `deposit(string)` with `msg.value > 0` on `RSETHPool.sol`. No role, whitelist, or special condition is required. The Arbitrum pool is actively used and holds a real rsETH balance. Likelihood: **Medium**.

### Recommendation

Port the `limitDailyMint` modifier and its associated state variables (`dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, `startTimestamp`) to `RSETHPool.sol` and apply it to both `deposit` overloads, exactly as done in `RSETHPoolV3ExternalBridge.sol` (reinitializer(4)) and the other V3 pool variants.

### Proof of Concept

1. Observe that `RSETHPoolV3ExternalBridge.sol` enforces `limitDailyMint` on every deposit path. [9](#0-8) 
2. Observe that `RSETHPool.sol` has no such modifier and no related state variables. [10](#0-9) 
3. Call `RSETHPool.deposit{value: X}("")` where `X` equals the pool's entire ETH-equivalent rsETH reserve. The call succeeds and transfers the full rsETH balance to the caller in one transaction.
4. Repeat with `RSETHPool.deposit(token, largeAmount, "")` for any supported token.
5. The pool's rsETH balance is now zero; all subsequent depositors receive `ERC20: transfer amount exceeds balance` reverts until the pool is manually replenished — a temporary freeze of the deposit facility for all other users.

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L130-159)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L226-235)
```text
     * - reinitializer(1): Initial deployment and setup of the contract with initial roles, wrsETH token, fees and
     * oracles. Only native ETH deposits used to be supported.
     * - reinitializer(2): Added support for ETH bridging via Stargate from L2 to L1.
     * - reinitializer(3): Upgraded to update the LayerZero V2 chain ID for the ETH mainnet.
     * - reinitializer(4): Introduced daily minting limit functionality to control the amount of wrsETH that can
     * be minted per day.
     * - reinitializer(5): Added a new supported token (wstETH), along with the oracle and the native bridging logic for
     * it.
     * - reinitializer(6): This upgrade enables native bridging of ETH from L2 to L1.
     */
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-399)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-293)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-329)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPool.sol (L35-90)
```text
contract RSETHPool is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
    using SafeERC20 for IERC20;

    /// @custom:oz-renamed-from rsETH
    IERC20Upgradeable public wrsETH;
    /// @custom:oz-renamed-from wstETH
    IERC20Upgradeable public legacyWstETH; // legacy variable

    uint256 public feeBps; // Basis points for fees for ETH deposits
    uint256 public feeEarnedInETH;
    /// @custom:oz-renamed-from feeEarnedInWstETH
    uint256 public legacyFeeEarnedInWstETH; // legacy variable

    address public rsETHOracle;
    /// @custom:oz-renamed-from wstETH_ETHOracle
    address public legacyWstETH_ETHOracle; // legacy variable
    /// @custom:oz-renamed-from MANAGER_ROLE
    bytes32 public constant LEGACY_MANAGER_ROLE = keccak256("MANAGER_ROLE");

    // new variables
    bytes32 public constant BRIDGER_ROLE = keccak256("BRIDGER_ROLE");
    bytes32 public constant TIMELOCK_ROLE = keccak256("TIMELOCK_ROLE");
    bool public isEthDepositEnabled;
    mapping(address token => uint256 feeEarned) public feeEarnedInToken;
    mapping(address token => address oracle) public supportedTokenOracle;
    address[] public supportedTokenList;

    /// @notice The corresponding L1Vault contract for the L2 chain
    address public l1VaultETHForL2Chain;
    /// @notice The StargatePool used for L2 --> L1 bridging
    IStargatePoolNative public stargatePool;
    /// @notice The LayerZero ID for the ETH mainnet
    uint32 public dstLzChainId;

    /// @notice The latest transaction receipt info from the StargatePoolNative
    TxReceipt public latestTxReceipt;

    /// @notice New variable added for pausable functionality
    bool public paused;

    /// @notice The mapping of token addresses to their respective token bridges
    mapping(address token => address bridge) public tokenBridge;

    /// @notice The address of the L2 bridge contract on Arbitrum
    address public l2Bridge;

    /// @notice The address of the Arbitrum messenger contract
    address public messenger;

    /// @notice The pauser role identifier
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    /// @dev Mapping of token to fee basis points
    mapping(address token => uint256 feeBps) public tokenFeeBps;

    modifier whenNotPaused() {
```

**File:** contracts/pools/RSETHPool.sol (L265-305)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```
