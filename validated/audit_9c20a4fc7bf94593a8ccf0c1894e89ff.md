Audit Report

## Title
Zero-Rate Division-by-Zero in `AGETHPoolV3.viewSwapAgETHAmountAndFee` Freezes All Deposits When `AGETHRateReceiver.rate == 0` — (`contracts/agETH/AGETHPoolV3.sol`)

## Summary

`CrossChainRateReceiver.rate` is a `uint256` storage variable that defaults to `0` and is only updated via `lzReceive`. `AGETHPoolV3.viewSwapAgETHAmountAndFee` divides by this rate with no zero-guard, causing a Solidity 0.8 division-by-zero panic on every `deposit` call until the first LayerZero rate message is received. This window exists at every deployment and every oracle replacement via `setAgETHOracle`, and can be extended by block stuffing.

## Finding Description

`CrossChainRateReceiver` declares `rate` as a plain `uint256` with no initialization: [1](#0-0) 

`AGETHRateReceiver`'s constructor sets `rateInfo`, `srcChainId`, `rateProvider`, and `layerZeroEndpoint`, but never sets `rate`: [2](#0-1) 

`getRate()` returns `rate` directly with no zero-check: [3](#0-2) 

`viewSwapAgETHAmountAndFee(uint256)` divides by the oracle rate unconditionally: [4](#0-3) 

The token-deposit overload does the same: [5](#0-4) 

Both `deposit` entrypoints call these view functions unconditionally: [6](#0-5) [7](#0-6) 

Critically, `addSupportedToken` guards against a zero rate for token oracles: [8](#0-7) 

But neither `initialize` nor `setAgETHOracle` applies any equivalent check for the `agETHOracle`: [9](#0-8) [10](#0-9) 

The exploit path: deploy `AGETHRateReceiver` (rate = 0) → call `initialize` with it as oracle (no zero-rate check fires) → any user calling `deposit{value: X}(ref)` triggers `viewSwapAgETHAmountAndFee` → division by zero → revert. No user funds are lost, but the entire deposit functionality is frozen until `lzReceive` is called with a non-zero rate.

## Impact Explanation

**Medium — Temporary freezing of funds.** All deposit functionality (both ETH and token paths) is frozen for any duration where `rate == 0`. This window exists naturally at every deployment and every oracle replacement. No funds are permanently lost, but users cannot deposit during this period.

## Likelihood Explanation

The zero-rate window exists at every deployment and every `setAgETHOracle` call — no attacker action is required to create it. A delayed or dropped LayerZero message produces the same effect passively. Block stuffing on lower-fee chains can extend the window actively, but even without an attacker, the asymmetry between the guarded `addSupportedToken` path and the unguarded `agETHOracle` path makes this a realistic operational risk.

## Recommendation

1. Add a zero-rate guard in `initialize` and `setAgETHOracle`, mirroring the existing check in `addSupportedToken`:
   ```solidity
   if (IOracle(_agETHOracle).getRate() == 0) revert UnsupportedOracle();
   ```
2. Add an explicit revert in `viewSwapAgETHAmountAndFee` if `agETHToETHrate == 0` rather than relying on the implicit Solidity panic.
3. Consider seeding `rate` with a safe initial value in the `AGETHRateReceiver` constructor, or requiring a successful `lzReceive` before the oracle can be set live.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import {AGETHPoolV3} from "contracts/agETH/AGETHPoolV3.sol";
import {AGETHRateReceiver} from "contracts/agETH/AGETHRateReceiver.sol";

contract ZeroRatePoC is Test {
    AGETHPoolV3 pool;
    AGETHRateReceiver receiver;

    function setUp() public {
        // Deploy receiver — rate is 0, no lzReceive ever called
        receiver = new AGETHRateReceiver(1, address(0x1), address(0x2));
        assertEq(receiver.getRate(), 0); // confirmed zero

        pool = new AGETHPoolV3();
        // initialize accepts oracle with rate == 0 — no guard fires
        pool.initialize(address(this), address(this), address(<agETH>), 10, address(receiver));
    }

    function testDepositRevertsOnZeroRate() public {
        vm.deal(address(this), 1 ether);
        vm.expectRevert(); // Solidity 0.8 division-by-zero panic
        pool.deposit{value: 1 ether}("ref");
    }
}
```

Running this against unmodified production code confirms the revert at `viewSwapAgETHAmountAndFee` line 168, freezing all deposit functionality whenever `AGETHRateReceiver.rate == 0`.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHRateReceiver.sol (L10-15)
```text
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "agETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L86-99)
```text
        UtilLib.checkNonZeroAddress(_agETH);
        UtilLib.checkNonZeroAddress(_agETHOracle);

        __ERC20_init("agETH", "agETH");
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        agETH = IERC20AgETH(_agETH);
        feeBps = _feeBps;
        agETHOracle = _agETHOracle;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L121-121)
```text
        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L147-147)
```text
        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L165-168)
```text
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L188-194)
```text
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L262-268)
```text
    function setAgETHOracle(address _agETHOracle) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(_agETHOracle);

        agETHOracle = _agETHOracle;

        emit OracleSet(_agETHOracle);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L279-281)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```
