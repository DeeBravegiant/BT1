### Title
Single-Step Ownership Transfer in Cross-Chain Rate Infrastructure Contracts - (File: contracts/cross-chain/CrossChainRateProvider.sol, contracts/cross-chain/CrossChainRateReceiver.sol, contracts/cross-chain/MultiChainRateProvider.sol)

### Summary
`CrossChainRateProvider`, `CrossChainRateReceiver`, and `MultiChainRateProvider` all inherit from OpenZeppelin's `Ownable`, which implements a single-step `transferOwnership`. If the owner accidentally transfers to an incorrect address, all owner-gated configuration functions become permanently inaccessible, freezing the cross-chain rate infrastructure and causing stale oracle rates on every L2 pool that depends on them.

### Finding Description
`CrossChainRateProvider` inherits `Ownable` and exposes `updateLayerZeroEndpoint`, `updateRateReceiver`, and `updateDstChainId` behind `onlyOwner`. [1](#0-0) 

`CrossChainRateReceiver` inherits `Ownable` and exposes `updateLayerZeroEndpoint`, `updateRateProvider`, and `updateSrcChainId` behind `onlyOwner`. [2](#0-1) 

`MultiChainRateProvider` inherits `Ownable` and exposes `updateLayerZeroEndpoint`, `addRateReceiver`, and `removeRateReceiver` behind `onlyOwner`. [3](#0-2) 

None of these contracts use a two-step ownership transfer (e.g., `Ownable2Step`). A single call to `transferOwnership(wrongAddress)` by the current owner immediately and irrevocably transfers control. There is no `pendingOwner` confirmation step and no recovery path. [4](#0-3) 

By contrast, `ConfirmedOwnerWithProposal` — used by `WrappedRSETH` — already implements the correct two-step pattern with `s_pendingOwner` and `acceptOwnership`. [5](#0-4) 

### Impact Explanation
If ownership of `MultiChainRateProvider` (concretely `RSETHMultiChainRateProvider` or `AGETHMultiChainRateProvider`) is accidentally transferred to an uncontrolled address:

- `addRateReceiver` / `removeRateReceiver` become permanently inaccessible, so no new L2 chain can be added and no broken receiver can be removed. [6](#0-5) 
- `updateLayerZeroEndpoint` becomes inaccessible, so if the LayerZero endpoint is upgraded the provider can no longer push rates. [7](#0-6) 

If ownership of `CrossChainRateReceiver` (concretely `RSETHRateReceiver` or `AGETHRateReceiver`) is lost:

- `updateRateProvider` becomes inaccessible, so the receiver can never be pointed at a new provider address. [8](#0-7) 
- `updateSrcChainId` becomes inaccessible, permanently locking the trusted source chain. [9](#0-8) 

The downstream effect is that `getRate()` on the receiver returns a permanently stale value. [10](#0-9) 

Every L2 pool (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`, `AGETHPoolV3`) reads `rsETHOracle.getRate()` to price deposits and compute wrsETH/rsETH mint amounts. A stale rate causes users to receive incorrect amounts — either over- or under-minted — without any loss of deposited principal. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**. [11](#0-10) 

### Likelihood Explanation
The trigger is an admin-level operational error (typo in address, copy-paste mistake, or deploying to the wrong network). No external attacker action is required. The probability is low but non-zero for any long-lived protocol with multiple deployments across chains. The consequence is permanent and unrecoverable without a contract redeployment.

### Recommendation
Replace `Ownable` with `Ownable2Step` (OpenZeppelin) in `CrossChainRateProvider`, `CrossChainRateReceiver`, and `MultiChainRateProvider`. `Ownable2Step` requires the nominated new owner to call `acceptOwnership()` before the transfer is finalised, exactly mirroring the pattern already used in `ConfirmedOwnerWithProposal`. [5](#0-4) 

### Proof of Concept
1. Owner of `RSETHRateReceiver` (which is `CrossChainRateReceiver`) calls `transferOwnership(0xDEAD)` — a single transaction, no confirmation required.
2. `s_owner` (the OZ `Ownable` `_owner` slot) is immediately set to `0xDEAD`.
3. The protocol later upgrades its LayerZero endpoint or deploys a new `RSETHMultiChainRateProvider`. The owner tries to call `updateRateProvider(newProvider)` — it reverts with `Ownable: caller is not the owner`.
4. `getRate()` on the receiver continues to return the last cached value indefinitely. [12](#0-11) 
5. All L2 pools reading this oracle now price every deposit against a stale rate, minting incorrect wrsETH amounts to depositors. [13](#0-12)

### Citations

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L12-12)
```text
abstract contract CrossChainRateProvider is Ownable, ReentrancyGuard {
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L57-79)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
    }

    /// @notice Updates the RateReceiver address
    /// @dev Can only be called by owner
    /// @param _rateReceiver the new rate receiver address
    function updateRateReceiver(address _rateReceiver) external onlyOwner {
        rateReceiver = _rateReceiver;

        emit RateReceiverUpdated(_rateReceiver);
    }

    /// @notice Updates the destination chainId
    /// @dev Can only be called by owner
    /// @param _dstChainId the destination chainId
    function updateDstChainId(uint16 _dstChainId) external onlyOwner {
        dstChainId = _dstChainId;

        emit DstChainIdUpdated(_dstChainId);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L11-11)
```text
abstract contract CrossChainRateReceiver is ILayerZeroReceiver, Ownable {
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L63-67)
```text
    function updateRateProvider(address _rateProvider) external onlyOwner {
        rateProvider = _rateProvider;

        emit RateProviderUpdated(_rateProvider);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L72-76)
```text
    function updateSrcChainId(uint16 _srcChainId) external onlyOwner {
        srcChainId = _srcChainId;

        emit SrcChainIdUpdated(_srcChainId);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-99)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L13-13)
```text
abstract contract MultiChainRateProvider is Ownable, ReentrancyGuard {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L62-65)
```text
    function updateLayerZeroEndpoint(address _layerZeroEndpoint) external onlyOwner {
        layerZeroEndpoint = _layerZeroEndpoint;

        emit LayerZeroEndpointUpdated(_layerZeroEndpoint);
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L72-102)
```text
    function addRateReceiver(uint16 _chainId, address _contract) external onlyOwner {
        rateReceivers.push(RateReceiver({ _chainId: _chainId, _contract: _contract }));

        emit RateReceiverAdded(_chainId, _contract);
    }

    /// @notice Removes a rate receiver
    /// @dev Can only be called by owner
    /// @param _index the index of the rate receiver
    function removeRateReceiver(uint256 _index) external onlyOwner {
        // Store the rate receiver in a memory var
        RateReceiver memory _rateReceiverToBeRemoved = rateReceivers[_index];

        // Get the current length of all the rate receivers
        uint256 rateReceiversLength = rateReceivers.length;

        // Get the last index of the all the rate receivers
        uint256 lastIndex = rateReceiversLength - 1;

        if (lastIndex != _index) {
            // Get the last rate receiver
            RateReceiver memory lastValue = rateReceivers[lastIndex];

            // Replace the index value with the last index value
            rateReceivers[_index] = lastValue;
        }

        rateReceivers.pop();

        emit RateReceiverRemoved(_rateReceiverToBeRemoved._chainId, _rateReceiverToBeRemoved._contract);
    }
```

**File:** contracts/ccip/ConfirmedOwnerWithProposal.sol (L43-51)
```text
    function acceptOwnership() external override {
        require(msg.sender == s_pendingOwner, "Must be proposed owner");

        address oldOwner = s_owner;
        s_owner = msg.sender;
        s_pendingOwner = address(0);

        emit OwnershipTransferred(oldOwner, msg.sender);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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
