### Title
Untracked ETH Received via `receive()` Causes TVL Undercount and Temporary Fund Freeze — (File: contracts/LRTConverter.sol)

### Summary
`LRTConverter` accepts ETH via a bare `receive() external payable {}` fallback, but any ETH that arrives through this path is never reflected in `ethValueInWithdrawal`. Because `LRTDepositPool.getETHDistributionData()` reads only `ethValueInWithdrawal` (not `address(lrtConverter).balance`) to account for ETH held in the converter, the extra ETH is invisible to the TVL calculation. Additionally, there is no sweep or recovery function; the only exit path for ETH is through `claimStEth`/`claimSwEth`, which require live Lido/Swell withdrawal requests. If none exist, the ETH is temporarily frozen.

### Finding Description
`LRTConverter` declares:

```solidity
receive() external payable { }
``` [1](#0-0) 

ETH legitimately enters the contract when Lido or Swell withdrawal claims are settled. However, ETH can also arrive via the fallback from any sender (accidental transfer, airdrop, selfdestruct proceeds). When that happens, `ethValueInWithdrawal` is **not** updated:

```solidity
// ethValueInWithdrawal is only mutated in transferAssetFromDepositPool / transferAssetToDepositPool
uint256 public ethValueInWithdrawal;
``` [2](#0-1) 

`LRTDepositPool.getETHDistributionData()` reads only this storage variable to represent the converter's contribution to TVL:

```solidity
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [3](#0-2) 

The actual `address(lrtConverter).balance` is never read. Extra ETH sitting in the contract is therefore invisible to the rsETH price oracle.

The only way ETH can leave `LRTConverter` is through `_sendEthToDepositPool`, which is called exclusively from `claimStEth` and `claimSwEth`:

```solidity
function claimStEth(uint256 _requestId, uint256 _hint) external nonReentrant onlyLRTOperator {
    _claimStEth(_requestId, _hint);
    _sendEthToDepositPool(address(this).balance);
}
``` [4](#0-3) 

Both functions require a valid, claimable Lido/Swell withdrawal request. If no such request exists, the ETH has no exit path. There is no `sweep()`, `recoverETH()`, or equivalent function in `LRTConverter`. [5](#0-4) 

A secondary accounting defect occurs when a claim *is* eventually made: `_sendEthToDepositPool(address(this).balance)` passes the **entire** balance (claimed ETH + extra ETH) and decrements `ethValueInWithdrawal` by that full amount:

```solidity
if (ethValueInWithdrawal > _amount) {
    ethValueInWithdrawal -= _amount;
} else {
    ethValueInWithdrawal = 0;
}
``` [6](#0-5) 

If multiple Lido requests are outstanding, `ethValueInWithdrawal` can reach zero prematurely, causing the remaining pending-withdrawal ETH to vanish from TVL until the next claim.

### Impact Explanation
**Temporary freezing of funds (Medium):** ETH that arrives via `receive()` when no Lido/Swell withdrawal request is claimable has no exit path. The operator must first transfer stETH into the converter, initiate a new Lido withdrawal, wait for the unbonding period, and then claim — a multi-day workaround. During this window the ETH is frozen inside the contract.

**TVL undercount / share mis-accounting (Low–Medium):** While the ETH is untracked, `rsETHPrice()` is lower than the true value. Depositors during this window receive more rsETH than they are entitled to, diluting existing holders. When the claim is eventually processed and `ethValueInWithdrawal` is zeroed prematurely, any remaining pending-withdrawal ETH also disappears from TVL, compounding the undercount.

### Likelihood Explanation
ETH can reach `LRTConverter.receive()` via accidental transfer, a `selfdestruct` of another contract, or an EigenLayer/Lido airdrop. The protocol already handles large ETH flows through this contract, making incidental ETH arrival plausible. The scenario where no Lido/Swell request is outstanding is less common but possible during quiet periods or after all requests have been claimed.

### Recommendation
1. Add a permissioned `sweepETH(address recipient)` function (callable by admin/manager) that forwards `address(this).balance` to the deposit pool or treasury when no active withdrawal is in progress.
2. Alternatively, in `_sendEthToDepositPool`, cap the decrement of `ethValueInWithdrawal` to the amount actually claimed (not `address(this).balance`) so that extra ETH does not corrupt the accounting.
3. Consider also adding a `recoverToken(address token, address recipient)` function for unsupported ERC-20 tokens that may arrive via airdrop, since `transferAssetToDepositPool` is restricted to `onlySupportedERC20Token`.

### Proof of Concept
1. Protocol has 100 stETH in a pending Lido withdrawal; `ethValueInWithdrawal = 100e18`.
2. An external party sends 10 ETH directly to `LRTConverter` (e.g., accidental transfer). `ethValueInWithdrawal` remains `100e18`; `address(lrtConverter).balance = 10 ETH`. TVL is understated by 10 ETH; rsETH price drops.
3. A depositor observes the lower rsETH price and deposits 50 ETH, receiving more rsETH than fair value.
4. Operator calls `claimStEth(requestId, hint)`. `address(this).balance = 110 ETH`. `_sendEthToDepositPool(110e18)` is called. Since `100e18 < 110e18`, `ethValueInWithdrawal` is set to `0` even though only one of potentially several requests was claimed.
5. Remaining pending Lido requests (say, another 50 ETH) are now invisible to TVL. rsETH price is further understated. The depositor from step 3 profits by withdrawing at the restored price once the operator claims the remaining requests.
6. If no Lido request exists at step 2, the 10 ETH has no exit path until the operator manually initiates a new stETH unstake cycle — a multi-day freeze.

### Citations

**File:** contracts/LRTConverter.sol (L43-43)
```text
    uint256 public ethValueInWithdrawal;
```

**File:** contracts/LRTConverter.sol (L117-118)
```text
    /// @dev fallback to receive funds
    receive() external payable { }
```

**File:** contracts/LRTConverter.sol (L180-183)
```text
    function claimStEth(uint256 _requestId, uint256 _hint) external nonReentrant onlyLRTOperator {
        _claimStEth(_requestId, _hint);
        _sendEthToDepositPool(address(this).balance);
    }
```

**File:** contracts/LRTConverter.sol (L252-263)
```text
    function _sendEthToDepositPool(uint256 _amount) internal {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
        emit EthTransferred(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```
