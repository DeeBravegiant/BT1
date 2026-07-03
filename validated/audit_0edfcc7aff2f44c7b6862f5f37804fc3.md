The code is fully readable. Let me analyze the vulnerability.

**Key facts from the code:**

1. `bridgeKernelToBSC` is `payable` and guarded by `onlyRole(OPERATOR_ROLE)`.
2. The ETH check at line 246 is `msg.value < nativeFee` — a lower-bound only, so `msg.value > nativeFee` is accepted.
3. At line 264, only exactly `nativeFee` is forwarded: `kernelOftAdapter.send{ value: nativeFee }(...)`.
4. The surplus `msg.value - nativeFee` remains in `KernelVaultETH` with no `receive()`, `fallback()`, or any ETH sweep/withdrawal function anywhere in the contract.
5. For comparison, `RSETHPoolV3ExternalBridge.bridgeAssets` uses an exact equality check (`msg.value != nativeFee` → revert) at line 689, preventing this exact issue.

---

### Title
Excess ETH sent to `bridgeKernelToBSC` is permanently locked — no recovery mechanism exists - (`contracts/KERNEL/KernelVaultETH.sol`)

### Summary
`KernelVaultETH.bridgeKernelToBSC` accepts `msg.value >= nativeFee` but only forwards exactly `nativeFee` to the OFT adapter. Any surplus ETH is permanently trapped in the contract because no withdrawal, sweep, or `receive`/`fallback` function exists.

### Finding Description
The guard at line 246 only enforces a lower bound:

```solidity
if (msg.value < nativeFee) {
    revert InsufficientNativeFee();
}
``` [1](#0-0) 

The adapter call at line 264 forwards only `nativeFee`:

```solidity
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
``` [2](#0-1) 

Any ETH in excess of `nativeFee` accumulates in `KernelVaultETH`. The contract defines no `receive()`, no `fallback()`, and no admin ETH-recovery function across its entire implementation. [3](#0-2) 

A sibling contract in the same codebase, `RSETHPoolV3ExternalBridge.bridgeAssets`, already applies the correct exact-equality pattern:

```solidity
if (msg.value != nativeFee) {
    revert IncorrectNativeFee();
}
``` [4](#0-3) 

### Impact Explanation
Any ETH sent above `nativeFee` is permanently frozen in `KernelVaultETH`. There is no admin rescue path. The correct impact classification is **Critical — Permanent freezing of funds**, not merely "temporary." The question's stated scope of "Medium / Temporary freezing" understates the severity: the funds are irrecoverable absent a contract upgrade.

### Likelihood Explanation
`bridgeKernelToBSC` is restricted to `OPERATOR_ROLE`. The operator is trusted, but operational mistakes (e.g., passing a stale or over-estimated `nativeFee` argument, or sending a round-number ETH value for convenience) are realistic. The absence of a recovery path means even a single such mistake causes permanent loss. The comparable function in the same repo demonstrates the team is aware of the exact-equality pattern, making the omission here a concrete oversight rather than a design choice.

### Recommendation
Replace the lower-bound check with an exact-equality check, mirroring `RSETHPoolV3ExternalBridge`:

```solidity
if (msg.value != nativeFee) {
    revert IncorrectNativeFee();
}
```

Alternatively, add an admin ETH-sweep function (e.g., `rescueETH`) protected by `DEFAULT_ADMIN_ROLE` so that any accidentally trapped ETH can be recovered.

### Proof of Concept
```solidity
// Preconditions: operator calls bridgeKernelToBSC with msg.value = nativeFee + 1 ether
vault.bridgeKernelToBSC{value: nativeFee + 1 ether}(amount, minAmount, nativeFee, refundAddress);

// Post-condition: surplus is permanently locked
assertEq(address(vault).balance, 1 ether);

// No function exists to recover it — confirmed by full contract read
// (no receive(), no fallback(), no withdrawETH(), no rescueETH())
``` [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L1-398)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.27;

import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import { IERC20 } from "@openzeppelin/contracts/interfaces/IERC20.sol";
import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import { PausableUpgradeable } from "@openzeppelin/contracts-upgradeable/security/PausableUpgradeable.sol";
import {
    ReentrancyGuardUpgradeable
} from "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import { IKERNEL_OFTAdapter, MessagingFee, SendParam } from "contracts/interfaces/IKERNEL_OFTAdapter.sol";
import { UtilLib } from "contracts/utils/UtilLib.sol";

/**
 * @title KernelVaultETH
 * @notice This contract is responsible for managing the deposits of KERNEL tokens on Ethereum mainnet and bridging them
 * to the Binance Smart Chain (BSC) via LayerZero where they will be restaked in the Kernel Protocol.
 */
contract KernelVaultETH is Initializable, AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
    using SafeERC20 for IERC20;

    /**
     * @notice Struct representing a user deposit
     * @param user The address of the user
     * @param amount The amount of KERNEL tokens deposited
     */
    struct UserDeposit {
        address user;
        uint256 amount;
    }

    /// @notice The operator role within the contract
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");

    /// @notice The merkle distributor role within the contract (can deposit on behalf of users)
    bytes32 public constant MERKLE_DISTRIBUTOR_ROLE = keccak256("MERKLE_DISTRIBUTOR_ROLE");

    /// @notice The KERNEL token contract on Ethereum mainnet
    IERC20 public kernel;

    /// @notice The Kernel OFT adapter contract
    IKERNEL_OFTAdapter public kernelOftAdapter;

    /// @notice The LayerZero chain ID of the BSC chain
    uint32 public dstLzChainId;

    /// @notice The address of the intended target (receiver) contract on the BSC chain
    address public receiver;

    /// @notice The minimum amount of KERNEL tokens expected for a deposit
    uint256 public minDeposit;

    /// @notice The next deposit ID to be set
    uint256 public counter;

    /// @notice The deposit ID of the last bridged deposit
    uint256 public lastBridgedDepositId;

    /// @notice The mapping of the deposit ID to the user deposit
    mapping(uint256 depositId => UserDeposit userDeposit) public userDeposits;

    /**
     * @notice Event emitted when a user deposits KERNEL tokens into the vault
     * @param depositId The deposit ID of the deposit
     * @param user The address of the user
     * @param amount The amount of KERNEL tokens deposited
     */
    event KernelVaultETHDeposit(uint256 depositId, address indexed user, uint256 amount);

    /**
     * @notice Event emitted when KERNEL tokens are bridged to the BSC chain
     * @param lzChainId The LayerZero chain ID of the BSC chain
     * @param receiver The address of the intended target (receiver) contract on the BSC chain
     * @param amount The amount of KERNEL tokens bridged
     * @param minAmount The minimum amount of KERNEL tokens expected on the BSC chain
     * @param nativeFee The native fee paid for the bridge
     * @param lastBridgedDepositId The deposit ID of the last bridged deposit
     */
    event BridgedKernelToBSC(
        uint32 indexed lzChainId,
        address indexed receiver,
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee,
        uint256 lastBridgedDepositId
    );

    /**
     * @notice Event emitted when the LayerZero chain ID of the BSC chain is updated
     * @param newDstLzChainId The new LayerZero chain ID of the BSC chain
     * @param oldDstLzChainId The old LayerZero chain ID of the BSC chain
     */
    event DstLzChainIdUpdated(uint32 indexed newDstLzChainId, uint32 indexed oldDstLzChainId);

    /**
     * @notice Event emitted when the receiver address is updated
     * @param newReceiver The new address of the intended target (receiver) contract on the BSC chain
     * @param oldReceiver The old address of the intended target (receiver) contract on the BSC chain
     */
    event ReceiverUpdated(address indexed newReceiver, address indexed oldReceiver);

    /**
     * @notice Event emitted when the minimum deposit amount is updated
     * @param newMinDeposit The new minimum amount of KERNEL tokens expected for a deposit
     * @param oldMinDeposit The old minimum amount of KERNEL tokens expected for a deposit
     */
    event MinDepositUpdated(uint256 indexed newMinDeposit, uint256 indexed oldMinDeposit);

    /// @notice Error message for when admin tries to set the invalid LayerZero chain ID
    error InvalidLzChainId();

    /// @notice Error message for when admin tries to set the invalid minimum deposit amount
    error InvalidMinDeposit();

    /// @notice Error message for when user tries to deposit an amount lower than the minimum
    error DepositAmountTooLow();

    /// @notice Error message for when the contract has insufficient KERNEL tokens to bridge
    error InsufficientKernelBalance();

    /// @notice Error message for an invalid minimum amount of KERNEL tokens expected on BSC after bridging
    error InvalidMinAmount();

    /// @notice Error message for an insufficient native fee sent for the bridge transaction
    error InsufficientNativeFee();

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /**
     * @dev Initialize the KernelVaultETH contract
     * @param _admin The address of the admin role
     * @param _operator The address of the operator role
     * @param _kernel The address of the KERNEL token contract on Ethereum mainnet
     * @param _kernelOftAdapter The address of the Kernel OFT adapter
     * @param _dstLzChainId The LayerZero chain ID of the BSC chain
     * @param _receiver The address of the intended target (receiver) contract on the BSC chain
     * @param _minDeposit The minimum amount of KERNEL tokens expected for a deposit
     */
    function initialize(
        address _admin,
        address _operator,
        address _kernel,
        address _kernelOftAdapter,
        uint32 _dstLzChainId,
        address _receiver,
        uint256 _minDeposit
    )
        external
        initializer
    {
        UtilLib.checkNonZeroAddress(_admin);
        UtilLib.checkNonZeroAddress(_operator);
        UtilLib.checkNonZeroAddress(_kernel);
        UtilLib.checkNonZeroAddress(_kernelOftAdapter);
        UtilLib.checkNonZeroAddress(_receiver);

        if (_dstLzChainId == 0) {
            revert InvalidLzChainId();
        }

        if (_minDeposit == 0) {
            revert InvalidMinDeposit();
        }

        __AccessControl_init();
        __Pausable_init();
        __ReentrancyGuard_init();

        _setupRole(DEFAULT_ADMIN_ROLE, _admin);
        _setupRole(OPERATOR_ROLE, _operator);

        kernel = IERC20(_kernel);
        kernelOftAdapter = IKERNEL_OFTAdapter(_kernelOftAdapter);
        dstLzChainId = _dstLzChainId;
        receiver = _receiver;
        minDeposit = _minDeposit;

        // Approve the Kernel OFT adapter to spend an unlimited amount of KERNEL tokens on behalf of this contract
        // for bridging purposes in order to avoid the need to approve the contract every time a bridging transaction
        // is initiated
        kernel.forceApprove(address(kernelOftAdapter), type(uint256).max);
    }

    /**
     * @notice Deposits KERNEL tokens into the vault
     * @param amount The amount of KERNEL tokens to deposit
     */
    function depositKernel(uint256 amount) external nonReentrant whenNotPaused {
        _depositKernel(msg.sender, amount);
    }

    /**
     * @notice Deposits KERNEL tokens into the vault on behalf of a user
     * @param user The address of the user
     * @param amount The amount of KERNEL tokens to deposit
     */
    function depositKernelFor(
        address user,
        uint256 amount
    )
        external
        nonReentrant
        whenNotPaused
        onlyRole(MERKLE_DISTRIBUTOR_ROLE)
    {
        _depositKernel(user, amount);
    }

    /*//////////////////////////////////////////////////////////////
                            Operator Actions
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Bridges KERNEL tokens to the BSC chain
     * @param amount The amount of KERNEL tokens to bridge
     * @param minAmount The minimum amount of KERNEL tokens to receive on BSC
     * @param nativeFee The native fee to pay for the bridge
     * @param refundAddress The address to refund the native fee to in case of a failed bridge transaction
     */
    function bridgeKernelToBSC(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee,
        address refundAddress
    )
        external
        payable
        nonReentrant
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(refundAddress);

        if (kernel.balanceOf(address(this)) < amount) {
            revert InsufficientKernelBalance();
        }

        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        if (msg.value < nativeFee) {
            revert InsufficientNativeFee();
        }

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        lastBridgedDepositId = counter - 1;

        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);

        emit BridgedKernelToBSC(dstLzChainId, receiver, amount, minAmount, nativeFee, lastBridgedDepositId);
    }

    /*//////////////////////////////////////////////////////////////
                            Admin Functions
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Sets the LayerZero chain ID of the BSC chain
     * @param _dstLzChainId The new LayerZero chain ID of the BSC chain
     */
    function setDstLzChainId(uint32 _dstLzChainId) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dstLzChainId == 0) {
            revert InvalidLzChainId();
        }

        uint32 oldDstLzChainId = dstLzChainId;
        dstLzChainId = _dstLzChainId;

        emit DstLzChainIdUpdated(_dstLzChainId, oldDstLzChainId);
    }

    /**
     * @notice Sets the receiver address
     * @param _receiver The new address of the intended target (receiver) contract on the BSC chain
     */
    function setReceiver(address _receiver) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(_receiver);

        address oldReceiver = receiver;
        receiver = _receiver;

        emit ReceiverUpdated(_receiver, oldReceiver);
    }

    /**
     * @notice Sets the minimum deposit amount
     * @param _minDeposit The new minimum amount of KERNEL tokens expected for a deposit
     */
    function setMinDeposit(uint256 _minDeposit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_minDeposit == 0) {
            revert InvalidMinDeposit();
        }

        uint256 oldMinDeposit = minDeposit;
        minDeposit = _minDeposit;

        emit MinDepositUpdated(_minDeposit, oldMinDeposit);
    }

    /// @notice Pauses the contract
    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    /// @notice Unpauses the contract
    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    /*//////////////////////////////////////////////////////////////
                            View Functions
    //////////////////////////////////////////////////////////////*/

    /**
     * @dev Quotes the native fee for bridging KERNEL tokens to BSC
     * @param amount The amount of KERNEL tokens to bridge
     * @param minAmount The minimum amount of KERNEL tokens to receive on BSC
     * @return The fee to be paid in native currency
     */
    function getNativeFee(uint256 amount, uint256 minAmount) external view returns (uint256) {
        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = kernelOftAdapter.quoteSend(sendParam, false);

        return fee.nativeFee;
    }

    /**
     * @dev Get the receiver address in the bytes32 format
     * @return The receiver address in the bytes32 format
     */
    function getReceiver() public view returns (bytes32) {
        return bytes32(uint256(uint160(receiver)));
    }

    /**
     * @dev Get the user deposit details
     * @param depositId The deposit ID
     * @return The user deposit details (user address and amount)
     */
    function getUserDeposit(uint256 depositId) external view returns (UserDeposit memory) {
        return userDeposits[depositId];
    }

    /*//////////////////////////////////////////////////////////////
                            Internal Functions
    //////////////////////////////////////////////////////////////*/

    /**
     * @dev Internal function to deposit KERNEL tokens into the vault
     * @param user The address of the user
     * @param amount The amount of KERNEL tokens to deposit
     */
    function _depositKernel(address user, uint256 amount) internal {
        UtilLib.checkNonZeroAddress(user);

        if (amount < minDeposit) {
            revert DepositAmountTooLow();
        }

        kernel.safeTransferFrom(msg.sender, address(this), amount);

        uint256 depositId = counter;

        userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
        ++counter;

        emit KernelVaultETHDeposit(depositId, user, amount);
    }
}
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L689-691)
```text
        if (msg.value != nativeFee) {
            revert IncorrectNativeFee();
        }
```
