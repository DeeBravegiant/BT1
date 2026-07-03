### Title
L2 Oracle Staleness Causes wrsETH Over-Issuance Relative to L1 rsETH Backing — (`contracts/pools/RSETHPoolV2ExternalBridge.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

`RSETHPoolV2ExternalBridge` and `RSETHPoolV3ExternalBridge` mint `wrsETH` on L2 using a locally-stored oracle rate. `L1Vault.depositETHForL1VaultETH()` mints the backing rsETH on L1 using the live `LRTOracle.rsETHPrice`. Because the L2 oracle is updated periodically and not continuously, a persistent lag exists where `L2_rate < L1_rate`. Every deposit during this lag mints more `wrsETH` than the rsETH that will ever back it, leaving `RsETHTokenWrapper` permanently undercollateralized.

---

### Finding Description

**Step 1 — L2 minting (over-issuance).**

In `RSETHPoolV2ExternalBridge.deposit()` and `RSETHPoolV3ExternalBridge.deposit()`:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate   // L2 oracle rate
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) [2](#0-1) 

`rsETHToETHrate` is read from `IOracle(rsETHOracle).getRate()` — a L2-local oracle that is updated by the protocol team on a schedule, not on every L1 price change.

**Step 2 — ETH bridges to L1, rsETH minted at true rate.**

`L1Vault.depositETHForL1VaultETH()` calls:

```solidity
uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);
lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
``` [3](#0-2) 

`getRsETHAmountToMint` uses `lrtOracle.rsETHPrice()` — the live, on-chain L1 price:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

**Step 3 — rsETH bridged back to `RsETHTokenWrapper`.**

`L1Vault.bridgeRsETHToL2()` sends the minted rsETH to `RsETHTokenWrapper` (the `l2Receiver`), which holds it as 1:1 backing for all outstanding `wrsETH`. [5](#0-4) 

**Step 4 — Structural shortfall.**

`RsETHTokenWrapper._withdraw()` burns `wrsETH` and transfers rsETH 1:1:

```solidity
_burn(msg.sender, _amount);
ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
``` [6](#0-5) 

`maxAmountToDepositBridgerAsset` exposes the gap:

```solidity
return wrsETHSupply - balanceOfAssetInWrapper;
``` [7](#0-6) 

When `wrsETHSupply > rsETH held`, the last redeemers cannot withdraw.

---

### Impact Explanation

**Numerical example** (L2 oracle stale by 10%):

| | Value |
|---|---|
| True L1 rsETH price | 1.10 ETH/rsETH |
| Stale L2 oracle rate | 1.00 ETH/rsETH |
| User deposits | 100 ETH |
| wrsETH minted on L2 | `100 / 1.00 = 100 wrsETH` |
| rsETH minted on L1 | `100 / 1.10 ≈ 90.9 rsETH` |
| Shortfall in wrapper | **9.1 rsETH** |

The `RsETHTokenWrapper` holds 90.9 rsETH but has 100 wrsETH outstanding. The last 9.1 wrsETH holders cannot redeem. Any user who sells their wrsETH on a secondary market at the implied 1:1 peg extracts value that other holders can never recover. This is **protocol insolvency through over-issuance**.

---

### Likelihood Explanation

- rsETH price grows continuously from staking rewards (~4–5% APY ≈ ~0.01% per day). Any gap between L2 oracle updates and L1 price creates a structural shortfall.
- No privileged access, oracle manipulation, or external compromise is required. Any depositor transacting while the L2 oracle is behind the L1 price contributes to the shortfall.
- The `dailyMintLimit` caps per-day exposure but does not prevent the undercollateralization from accumulating across days or from a single large deposit within the limit.
- The `RSETHPool` (Arbitrum) is not affected because it transfers pre-held `wrsETH` rather than minting fresh tokens. [8](#0-7) [9](#0-8) 

---

### Recommendation

1. **Reconcile at bridge time**: When ETH arrives at `L1Vault`, compute the rsETH that will be minted and compare it to the wrsETH already issued for that batch. Revert or flag if the L1 mint is less than the L2 issuance.
2. **Enforce a maximum oracle staleness**: Add a `lastUpdatedAt` timestamp to the L2 oracle and revert `deposit()` if the oracle is older than a threshold (e.g., 1 hour).
3. **Mint wrsETH lazily**: Instead of minting wrsETH immediately on deposit, issue a claim ticket and only mint wrsETH after the corresponding rsETH has been confirmed on L1 and bridged back.
4. **Tighten the daily mint limit**: Set the limit conservatively relative to the expected oracle update frequency and maximum price drift.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (local fork, no public mainnet)
// Preconditions:
//   - L2 oracle returns 1.0e18 (stale)
//   - L1 LRTOracle.rsETHPrice() returns 1.1e18 (true)
//   - dailyMintLimit >= 100e18

function testOracleStalenessCausesInsolvency() public {
    // 1. Attacker deposits 100 ETH on L2 while oracle is stale
    uint256 depositAmount = 100 ether;
    vm.deal(attacker, depositAmount);
    vm.prank(attacker);
    pool.deposit{value: depositAmount}("ref");

    uint256 wrsETHMinted = wrsETH.balanceOf(attacker);
    // wrsETHMinted = 100e18 / 1.0e18 = 100e18

    // 2. Bridger bridges ETH to L1
    vm.prank(bridger);
    pool.bridgeAssets(depositAmount - pool.feeEarnedInETH(), minAmount, nativeFee);

    // 3. L1Vault deposits ETH at true L1 rate
    vm.prank(manager);
    l1Vault.depositETHForL1VaultETH();
    uint256 rsETHMintedOnL1 = rsETH.balanceOf(address(l1Vault));
    // rsETHMintedOnL1 ≈ 90.9e18 (100e18 / 1.1e18)

    // 4. L1Vault bridges rsETH back to RsETHTokenWrapper
    vm.prank(manager);
    l1Vault.bridgeRsETHToL2(rsETHMintedOnL1, rsETHMintedOnL1, nativeFee);

    // 5. Assert: wrsETH supply > rsETH backing → insolvency
    uint256 wrsETHSupply = wrsETH.totalSupply();
    uint256 rsETHBacking = rsETH.balanceOf(address(wrapper));
    assertGt(wrsETHSupply, rsETHBacking, "wrsETH over-issued: insolvency");
    // wrsETHSupply = 100e18, rsETHBacking ≈ 90.9e18
    // shortfall ≈ 9.1e18 rsETH
}
``` [10](#0-9) [3](#0-2) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L102-126)
```text
    /// @dev Modifier to enforce the daily minting limit
    /// @param amount The ETH amount sent in the deposit
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L307-315)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L127-159)
```text
    /// @dev Modifier to enforce the daily minting limit
    /// @param amount The asset amount sent in the deposit
    /// @param token The token address
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

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

**File:** contracts/L1Vault.sol (L218-257)
```text
    function bridgeRsETHToL2(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee
    )
        external
        payable
        nonReentrant
        onlyRole(MANAGER_ROLE)
    {
        if (rsETH.balanceOf(address(this)) < amount) {
            revert InsufficientRsETHBalance();
        }

        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        if (msg.value != nativeFee) {
            revert IncorrectNativeFee();
        }

        IERC20(address(rsETH)).safeIncreaseAllowance(address(oftAdapter), amount);

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

        oftAdapter.send{ value: nativeFee }(sendParam, fee, msg.sender);

        emit BridgedRsETHToL2(dstLzChainId, l2Receiver, amount, minAmount);
    }
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```
