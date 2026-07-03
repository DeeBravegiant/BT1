### Title
No Emergency ETH/Token Withdrawal in L1Vault Causes Fund Freeze When Deposit Pool Is Paused - (File: contracts/L1Vault.sol)

### Summary
`L1Vault` and `L1VaultV2` receive ETH from L2 bridges via an open `receive()` function but expose no emergency withdrawal path. The sole ETH egress is `depositETHForL1VaultETH()`, which requires `LRTDepositPool` to be operational and unpaused. When the deposit pool is paused — a routine operational action — ETH bridged from L2 is frozen in `L1Vault` with no recovery mechanism.

### Finding Description
Both `L1Vault` and `L1VaultV2` are designed to receive ETH from L2 chains via native bridges and convert it to rsETH by depositing into `LRTDepositPool`. The contracts expose:

- `receive() external payable {}` — accepts ETH from any sender, including L2 bridge contracts, with no restrictions.
- `depositETHForL1VaultETH()` — the **sole ETH egress path**, which calls `lrtDepositPool.depositETH{value: balanceOfETH}(rsETHAmountToMint, "")` and reverts if `rsETHAmountToMint == 0` or if the deposit pool is paused.
- `depositAssetForL1Vault(address token)` — the **sole ERC20 egress path**, which similarly requires the deposit pool to be functional.

Neither contract contains an admin-controlled `withdraw()`, `emergencyWithdraw()`, or any other rescue function. If `LRTDepositPool` is paused, all ETH and LST tokens held in `L1Vault` are frozen with no recovery path until the pool is unpaused.

The `receive()` function in `L1Vault`: [1](#0-0) 

The sole ETH egress path, which requires the deposit pool to be functional: [2](#0-1) 

The same pattern is replicated in `L1VaultV2`: [3](#0-2) [4](#0-3) 

The `LRTDepositPool.depositETH()` carries `whenNotPaused`, meaning any pause of the deposit pool blocks the only egress: [5](#0-4) 

### Impact Explanation
ETH and LST tokens (stETH, wstETH, WETH) bridged from L2 to `L1Vault` are frozen whenever `LRTDepositPool` is paused. During this period the protocol cannot convert bridged ETH to rsETH, meaning wrsETH issued on L2 is not backed by newly minted rsETH. If the deposit pool is permanently deprecated without a migration path, the freeze becomes permanent — **Critical: permanent freezing of funds**. In the more common case of a temporary pause, the impact is **Medium: temporary freezing of funds**.

### Likelihood Explanation
`LRTDepositPool` has a `pause()` function callable by the `PAUSER_ROLE`. Pausing is a routine operational action during security incidents, oracle anomalies, or protocol upgrades. The L2 bridge operates asynchronously and will deliver ETH to `L1Vault` regardless of the deposit pool's state. It is realistic that ETH arrives at `L1Vault` while the deposit pool is paused, leaving it with no egress path.

### Recommendation
Add an admin-controlled emergency withdrawal function to both `L1Vault` and `L1VaultV2`:

```solidity
function emergencyWithdrawETH(address payable recipient, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool success,) = recipient.call{value: amount}("");
    require(success, "Transfer failed");
    emit EmergencyETHWithdrawn(recipient, amount);
}

function emergencyWithdrawToken(address token, address recipient, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    IERC20(token).safeTransfer(recipient, amount);
    emit EmergencyTokenWithdrawn(token, recipient, amount);
}
```

### Proof of Concept
1. `LRTDepositPool` is paused by the pauser role (routine security action).
2. The L2 bridge delivers ETH to `L1Vault.receive()` — ETH is now held in `L1Vault`.
3. Manager calls `depositETHForL1VaultETH()` → internally calls `lrtDepositPool.depositETH{value: balance}(...)` → reverts because `LRTDepositPool` is paused (`whenNotPaused` modifier).
4. No other function in `L1Vault` can move the ETH out.
5. ETH remains frozen until the deposit pool is unpaused, with no emergency recovery path available.

### Citations

**File:** contracts/L1Vault.sol (L150-161)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
    }
```

**File:** contracts/L1Vault.sol (L367-368)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/L1VaultV2.sol (L224-234)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
```

**File:** contracts/L1VaultV2.sol (L562-563)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```
