### Title
Missing Balance Validation in `depositAsset` Allows rsETH Over-Minting for Fee-on-Transfer Tokens - (File: `contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool.depositAsset()` calculates the rsETH minting amount from the nominal `depositAmount` parameter **before** the actual `safeTransferFrom` is executed, with no balance-before/after check to verify the actual received amount. This is the direct analog of the reported vulnerability class: state updates based on an assumed transfer amount rather than a verified received amount.

### Finding Description
In `LRTDepositPool.depositAsset()`, the rsETH minting amount is computed via `_beforeDeposit` using the caller-supplied `depositAmount` before the token transfer occurs:

```solidity
function depositAsset(address asset, uint256 depositAmount, ...) external {
    uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected); // uses depositAmount
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);                 // actual transfer
    _mintRsETH(rsethAmountToMint);                                                            // mints based on nominal amount
}
``` [1](#0-0) 

`_beforeDeposit` calls `getRsETHAmountToMint(asset, depositAmount)`, which computes the rsETH amount from the oracle price and the nominal `depositAmount`: [2](#0-1) 

There is no balance-before/after check to confirm the actual amount received. By contrast, `KernelDepositPool.notifyRewardAmount()` in the same repository correctly uses the balance-delta pattern: [3](#0-2) 

If a fee-on-transfer ERC-20 is ever added as a supported asset via `addNewSupportedAsset`, the contract receives `depositAmount − fee` tokens but mints rsETH as if it received the full `depositAmount`. The deposit-limit check in `_checkIfDepositAmountExceedesCurrentLimit` also uses the nominal amount, so the limit is consumed faster than actual assets arrive. [4](#0-3) 

### Impact Explanation
Every deposit with a fee-on-transfer token inflates the rsETH supply relative to actual protocol assets. The rsETH price (`_updateRsETHPrice`) is computed from real on-chain balances divided by total rsETH supply: [5](#0-4) 

Because the supply is inflated while actual assets are not, the rsETH price is depressed below its true value. Existing rsETH holders are diluted on every such deposit, and the protocol drifts toward insolvency — a Critical impact (protocol insolvency / permanent dilution of user funds).

### Likelihood Explanation
Low. The current supported assets (stETH, ETHx) are not fee-on-transfer tokens. Exploitation requires an admin to add a fee-on-transfer ERC-20 via `addNewSupportedAsset`. This is a legitimate admin action that the code does not guard against, not a key compromise. As the protocol expands its supported asset list, the probability increases.

### Recommendation
Replace the nominal-amount pattern with a balance-before/after check, mirroring the pattern already used in `KernelDepositPool.notifyRewardAmount()`:

```solidity
function depositAsset(address asset, uint256 depositAmount, ...) external {
    uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
    uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

    uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit AssetDeposit(msg.sender, asset, actualReceived, rsethAmountToMint, referralId);
}
```

### Proof of Concept
1. Admin calls `addNewSupportedAsset(feeToken, depositLimit)` where `feeToken` charges a 1% transfer fee.
2. User calls `depositAsset(feeToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `1000e18`.
4. `safeTransferFrom` transfers only `990e18` to the contract (1% fee deducted).
5. `_mintRsETH` mints rsETH for `1000e18` worth of assets.
6. Protocol now holds `990e18` tokens but has issued rsETH for `1000e18`.
7. Repeated deposits continuously inflate rsETH supply relative to actual assets, depressing rsETH price and diluting all existing holders. [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L573-578)
```text
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;

```

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
