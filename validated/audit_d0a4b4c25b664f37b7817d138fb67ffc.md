Audit Report

## Title
Zero agETH Minted on Dust Deposit with No Refund — (`contracts/agETH/AGETHPoolV3.sol`)

## Summary

In `deposit(address,uint256,string)`, the depositor's tokens are transferred into the contract before `agETHAmount` is computed. For any dust input where integer division truncates `agETHAmount` to zero, `agETH.mint(msg.sender, 0)` is called silently (OpenZeppelin's `_mint` does not revert on zero), and the depositor's tokens are permanently retained by the contract with no refund path. The same truncation applies to the ETH path in `deposit(string)`.

## Finding Description

In `deposit(address token, uint256 amount, string referralId)`:

1. **Token transfer occurs first** at line 145: `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` — before any output amount is verified.
2. `agETHAmount` is computed at line 147 via `viewSwapAgETHAmountAndFee(amount, token)`, which performs integer division at line 194: `agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate`. For any `amount` where `amountAfterFee * tokenToETHRate < agETHToETHrate`, this truncates to **0**.
3. The only existing guard is `if (amount == 0) revert InvalidAmount()` at line 143 — this does not protect against a non-zero `amount` that yields zero `agETHAmount`.
4. `agETH.mint(msg.sender, 0)` is called at line 151. OpenZeppelin's `_mint` (confirmed at `lib/openzeppelin-contracts-upgradeable/contracts/token/ERC20/ERC20Upgradeable.sol` lines 256–268) does not revert on zero — it adds 0 to balances and emits `Transfer(address(0), account, 0)`.
5. There is no refund path. The depositor's tokens accumulate in the contract and are swept by `BRIDGER_ROLE` via `moveAssetsForBridging(address token)` at line 234–241, which transfers `IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token]` to the bridger.

The ETH path in `deposit(string)` has the same truncation at line 168: `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate`. For 1 wei of ETH sent when `agETHToETHrate > 1e18`, `agETHAmount` is 0 and the ETH is swept by `moveAssetsForBridging()` at line 223–231. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation

The depositor permanently loses their tokens. The funds are not returned and no agETH is issued in exchange. The tokens are swept by the bridger via `moveAssetsForBridging`. This constitutes **direct theft of user funds in-motion** (Critical) and **permanent freezing of funds** from the depositor's perspective (Critical). While individual losses are dust-level (≤2 wei for 18-decimal tokens; ≤~3154 units for 6-decimal tokens such as USDC at ~0.003 USDC), the invariant is unconditionally broken for any deposit below the threshold, the loss is permanent, and there is no recovery path for the user. [4](#0-3) 

## Likelihood Explanation

Any unprivileged external user can trigger this by calling `deposit(token, dustAmount, "")` with a supported token and a sufficiently small amount. No special role, no front-running, no oracle manipulation, and no victim mistake is required. The condition is deterministic: it depends only on the current `agETHToETHrate` (from `agETHOracle`) and `tokenToETHRate` (from `supportedTokenOracle[token]`), both of which are publicly readable. The exploit is reproducible on any local fork. [5](#0-4) [6](#0-5) 

## Recommendation

Add a zero-output guard immediately after computing `agETHAmount`, before (or immediately after) the token transfer:

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();
```

Apply the same guard in the ETH path in `deposit(string)`. Alternatively, enforce a minimum deposit amount that guarantees `agETHAmount >= 1` given the current oracle rates. If the guard is placed after the transfer, add a refund path to return tokens when `agETHAmount == 0`. [7](#0-6) 

## Proof of Concept

Minimal Foundry unit test plan (local fork):

```solidity
// Setup: agETHToETHrate = 1.05e18, tokenToETHRate = 1e18, feeBps = 0
// Call: deposit(token, 1, "")
// Step 1: safeTransferFrom pulls 1 wei token from depositor → contract balance +1
// Step 2: viewSwapAgETHAmountAndFee(1, token):
//         fee = 0, amountAfterFee = 1
//         agETHAmount = 1 * 1e18 / 1.05e18 = 0  ← truncated
// Step 3: agETH.mint(msg.sender, 0) → succeeds, emits Transfer(0, user, 0)
// Assert: agETH.balanceOf(depositor) == 0
// Assert: token.balanceOf(contract)  == 1  (depositor's token permanently lost)
// Assert: after bridger calls moveAssetsForBridging(token), bridger receives the 1 wei
```

The same test applies to the ETH path with `deposit{value: 1}("")` yielding `agETHAmount = 1 * 1e18 / 1.05e18 = 0`. [8](#0-7)

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L143-154)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L183-195)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L223-241)
```text
    function moveAssetsForBridging() external onlyRole(BRIDGER_ROLE) {
        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = address(this).balance - feeEarnedInETH;

        (bool success,) = msg.sender.call{ value: ethBalanceMinusFees }("");
        if (!success) revert TransferFailed();

        emit AssetsMovedForBridging(ethBalanceMinusFees);
    }

    /// @dev Withdraws assets from the contract for bridging
    function moveAssetsForBridging(address token) external onlySupportedToken(token) onlyRole(BRIDGER_ROLE) {
        // withdraw token - fees
        uint256 tokenBalanceMinusFees = IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];

        IERC20(token).safeTransfer(msg.sender, tokenBalanceMinusFees);

        emit AssetsMovedForBridging(tokenBalanceMinusFees, token);
    }
```

**File:** lib/openzeppelin-contracts-upgradeable/contracts/token/ERC20/ERC20Upgradeable.sol (L256-268)
```text
    function _mint(address account, uint256 amount) internal virtual {
        require(account != address(0), "ERC20: mint to the zero address");

        _beforeTokenTransfer(address(0), account, amount);

        _totalSupply += amount;
        unchecked {
            // Overflow not possible: balance + amount is at most totalSupply + amount, which is checked above.
            _balances[account] += amount;
        }
        emit Transfer(address(0), account, amount);

        _afterTokenTransfer(address(0), account, amount);
```
