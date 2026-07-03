### Title
Division-by-Zero in `viewSwapAgETHAmountAndFee` When `AGETHRateReceiver.rate` Is Uninitialized Freezes All Deposits — (`contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHPoolV3` uses `AGETHRateReceiver` (a `CrossChainRateReceiver`) as its `agETHOracle`. The receiver's `rate` storage slot defaults to `0` until the first LayerZero `lzReceive` message is delivered. Both `deposit` overloads call `viewSwapAgETHAmountAndFee`, which divides by `agETHToETHrate`. When that value is `0`, Solidity 0.8's checked arithmetic reverts unconditionally, freezing all deposit flows for any depositor until the cross-chain rate message arrives.

---

### Finding Description

`CrossChainRateReceiver.rate` is a plain `uint256` storage variable with no explicit initialization: [1](#0-0) 

Its `getRate()` returns this value with no zero-guard: [2](#0-1) 

`AGETHRateReceiver`'s constructor sets configuration fields but never seeds `rate`: [3](#0-2) 

`rate` is only written inside `lzReceive`, which requires a live LayerZero message from the source chain: [4](#0-3) 

`AGETHPoolV3.getRate()` forwards directly to the oracle with no zero-check: [5](#0-4) 

Both `viewSwapAgETHAmountAndFee` overloads divide by `agETHToETHrate`: [6](#0-5) [7](#0-6) 

Both `deposit` overloads call these functions unconditionally: [8](#0-7) [9](#0-8) 

The asymmetry is telling: `addSupportedToken` explicitly guards against a zero-rate token oracle: [10](#0-9) 

But neither `initialize` nor `setAgETHOracle` applies the same guard to `agETHOracle`: [11](#0-10) [12](#0-11) 

---

### Impact Explanation

Every call to `deposit(string)` or `deposit(address,uint256,string)` reverts with a division-by-zero panic while `rate == 0`. `isEthDepositEnabled` is set to `true` in `initialize`, so the contract advertises deposits as open, yet no depositor can succeed. This is a **temporary freezing of all deposit flows** — matching the Medium scope target — lasting from deployment until the first successful `lzReceive` delivery. If the LayerZero message is dropped or delayed, the freeze extends indefinitely without any on-chain recovery path available to non-admin users.

---

### Likelihood Explanation

This condition is reached on **every fresh deployment** before the first cross-chain rate message arrives. No attacker action is required; any ordinary depositor triggers it. The window is bounded by LayerZero liveness but is non-zero by construction and can be extended by message drops or network congestion.

---

### Recommendation

Add a zero-rate guard in `viewSwapAgETHAmountAndFee` (both overloads) before the division:

```solidity
if (agETHToETHrate == 0) revert UnsupportedOracle();
```

Alternatively, add the same guard used for token oracles to `initialize` and `setAgETHOracle`:

```solidity
if (IOracle(_agETHOracle).getRate() == 0) revert UnsupportedOracle();
```

The second approach prevents misconfiguration at setup time but does not protect against the rate later becoming zero after a receiver reset. Both guards together provide full coverage.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/agETH/AGETHRateReceiver.sol";
import "contracts/agETH/AGETHPoolV3.sol";

contract MockAgETH {
    function mint(address, uint256) external {}
    // minimal ERC20 stubs omitted for brevity
}

contract DivByZeroTest is Test {
    AGETHRateReceiver receiver;
    AGETHPoolV3 pool;

    function setUp() public {
        // Deploy receiver — lzReceive never called, rate == 0
        receiver = new AGETHRateReceiver(
            101,                        // srcChainId (arbitrary)
            address(0xBEEF),            // rateProvider
            address(0xDEAD)             // layerZeroEndpoint
        );

        MockAgETH agETH = new MockAgETH();

        pool = new AGETHPoolV3();
        pool.initialize(
            address(this),
            address(this),
            address(agETH),
            0,                          // feeBps
            address(receiver)           // agETHOracle with rate == 0
        );
    }

    function test_depositRevertsWhenRateIsZero() public {
        // Confirm rate is 0
        assertEq(receiver.getRate(), 0);

        // ETH deposit reverts
        vm.deal(address(this), 1 ether);
        vm.expectRevert(); // division-by-zero panic
        pool.deposit{value: 1 ether}("ref");
    }
}
```

Running this test against unmodified contracts will confirm the revert, proving the deposit freeze.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
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

**File:** contracts/agETH/AGETHPoolV3.sol (L76-101)
```text
    function initialize(
        address admin,
        address bridger,
        address _agETH,
        uint256 _feeBps,
        address _agETHOracle
    )
        external
        initializer
    {
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
        isEthDepositEnabled = true;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L104-106)
```text
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
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
