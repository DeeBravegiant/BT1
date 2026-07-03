### Title
L2 Oracle Staleness Enables Structural Over-Issuance of wrsETH Relative to L1 rsETH Backing — (`contracts/pools/RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPool.sol`)

---

### Summary

The L2 pool contracts mint wrsETH **immediately** at the L2 oracle rate on every `deposit()` call, while the corresponding L1 rsETH is only minted **later** (by a privileged operator) at the then-current L1 `LRTOracle.rsETHPrice`. Because rsETH's exchange rate monotonically increases over time as yield accrues, a stale L2 oracle (showing an older, lower rate) causes the L2 pool to issue **more** wrsETH per ETH than the L1 will ever back. There is no staleness check, no cross-chain rate reconciliation, and no on-chain enforcement that the L2 oracle matches the L1 rate before minting. Any depositor who deposits while the L2 oracle lags the L1 rate receives excess wrsETH that is permanently unbacked.

---

### Finding Description

**Step 1 — L2 deposit mints wrsETH at L2 oracle rate immediately.**

In all three pool variants the mint formula is identical:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate   // L2 oracle rate
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The L2 oracle is a separate contract set by `TIMELOCK_ROLE` via `setRSETHOracle()`. It is **not** the L1 `LRTOracle` — it is a distinct contract that must be kept in sync with L1 out-of-band. [4](#0-3) 

**Step 2 — ETH is bridged to L1 by a privileged bridger (not the depositor).**

`bridgeAssets()` is gated to `BRIDGER_ROLE` and sends the accumulated ETH to `L1Vault` via Stargate/LayerZero. The depositor has no control over when this happens. [5](#0-4) 

**Step 3 — L1 mints rsETH at the current L1 rate (not the L2 oracle rate).**

`L1Vault.depositETHForL1VaultETH()` calls `lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH)`, which uses `LRTOracle.rsETHPrice` — the live, on-chain L1 rate. [6](#0-5) 

**Step 4 — The L1 `LRTOracle.rsETHPrice` is a monotonically increasing value.**

`updateRSETHPrice()` computes `rsETHPrice = totalETHInProtocol / rsETHSupply`. As yield accrues, this value only increases. A stale L2 oracle therefore always shows a **lower** rate than the current L1 rate. [7](#0-6) 

**The invariant break:**

| Variable | Value (example) |
|---|---|
| L2 oracle rate (stale) | 1.05 ETH/rsETH |
| L1 oracle rate (current) | 1.10 ETH/rsETH |
| ETH deposited | 100 ETH |
| wrsETH minted on L2 | 100 / 1.05 ≈ **95.24 rsETH** |
| rsETH minted on L1 | 100 / 1.10 ≈ **90.91 rsETH** |
| **Unbacked over-issuance** | **4.33 rsETH** |

There is **no staleness check** anywhere in the pool contracts before minting, and **no reconciliation mechanism** that compares L2 wrsETH outstanding against L1 rsETH minted.

---

### Impact Explanation

Every deposit made while the L2 oracle lags the L1 rate produces permanently unbacked wrsETH. The wrsETH is redeemable for L1 rsETH via the `RsETHTokenWrapper`, but the L1 rsETH pool backing it is smaller than the wrsETH supply. Repeated deposits across the staleness window accumulate the deficit. At scale this constitutes **protocol insolvency through over-issuance**: the total wrsETH outstanding on L2 exceeds the rsETH that can ever be bridged back to cover it.

The daily mint limit (`dailyMintLimit`) bounds the per-day magnitude but does not prevent the structural deficit from accumulating day after day whenever the oracle lags. [8](#0-7) 

---

### Likelihood Explanation

rsETH's exchange rate increases continuously. Any push-based or manually-updated L2 oracle will periodically lag the L1 rate — this is a normal operating condition, not an exceptional event. No oracle compromise is required. A depositor only needs to observe that the L2 oracle rate is below the L1 rate (both are publicly readable on-chain) and deposit during that window. The gap between oracle updates can be hours to days depending on the update cadence, giving ample time for exploitation.

---

### Recommendation

1. **Add a staleness / rate-ceiling check in `deposit()`**: reject or cap minting if the L2 oracle rate deviates from a trusted cross-chain rate by more than a configurable threshold.
2. **Enforce a maximum L2 oracle age**: store the timestamp of the last oracle update and revert deposits if the oracle is older than a defined heartbeat (e.g., 1 hour).
3. **Reconcile at bridge time**: track total wrsETH minted per bridge cycle and compare against the rsETH actually minted on L1 after `depositETHForL1VaultETH()` completes; pause L2 minting if a deficit is detected.
4. **Use a pull-based cross-chain oracle** (e.g., LayerZero `lzRead` or a Chainlink CCIP rate feed) so the L2 oracle is updated atomically with L1 rate changes.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Differential fork test (Foundry) — run against a local fork
// Demonstrates: L2 wrsETH minted > L1 rsETH backed when L2 oracle is stale

contract StaleOraclePoC is Test {
    // Assume addresses are set to deployed contracts on a local fork
    RSETHPoolV2ExternalBridge l2Pool;
    L1Vault l1Vault;
    MockStaleOracle staleOracle; // returns 0.9x the true L1 rate

    function setUp() public {
        // Deploy stale oracle returning 0.9 * trueRate
        uint256 trueL1Rate = ILRTOracle(L1_ORACLE).rsETHPrice(); // e.g. 1.10e18
        staleOracle = new MockStaleOracle(trueL1Rate * 9 / 10);   // 0.99e18

        // Admin sets stale oracle on L2 pool (via TIMELOCK_ROLE in test)
        vm.prank(timelockAdmin);
        l2Pool.setRSETHOracle(address(staleOracle));
    }

    function test_overIssuance() public {
        uint256 depositAmount = 100 ether;
        address depositor = address(0xBEEF);
        vm.deal(depositor, depositAmount);

        // Step 1: deposit on L2 at stale rate
        vm.prank(depositor);
        l2Pool.deposit{value: depositAmount}("ref");
        uint256 wrsETHMinted = wrsETH.balanceOf(depositor);

        // Step 2: bridge ETH to L1 (operator action)
        vm.prank(bridger);
        l2Pool.bridgeAssets(depositAmount, depositAmount * 99 / 100, nativeFee);

        // Step 3: mint rsETH on L1 at true rate
        vm.prank(manager);
        l1Vault.depositETHForL1VaultETH();
        uint256 rsETHMintedOnL1 = rsETH.balanceOf(address(l1Vault));

        // Assert: L2 issued more than L1 backed
        assertGt(wrsETHMinted, rsETHMintedOnL1,
            "L2 wrsETH > L1 rsETH: protocol insolvency confirmed");

        // Concrete numbers with 0.9x stale oracle:
        // wrsETHMinted  ≈ 100e18 / 0.99e18 ≈ 101.01 rsETH
        // rsETHMintedOnL1 ≈ 100e18 / 1.10e18 ≈ 90.91 rsETH
        // Deficit ≈ 10.1 rsETH on 100 ETH deposited
    }
}
```

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L104-126)
```text
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L307-316)
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L486-527)
```text
    function bridgeAssets(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee
    )
        external
        payable
        nonReentrant
        onlyRole(BRIDGER_ROLE)
    {
        // Exclude msg.value so reserved fees can’t be accidentally consumed
        if (getETHBalanceMinusFees() - msg.value < amount) {
            revert InsufficientETHBalance();
        }

        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        if (msg.value != nativeFee) {
            revert IncorrectNativeFee();
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

        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);

        latestTxReceipt = TxReceipt({ guid: msgReceipt.guid, amountReceivedLD: oftReceipt.amountReceivedLD });

        emit BridgedETHToL1(dstLzChainId, l1VaultETHForL2Chain, oftReceipt.amountSentLD, oftReceipt.amountReceivedLD);
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L539-543)
```text
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
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

**File:** contracts/pools/RSETHPool.sol (L311-320)
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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
