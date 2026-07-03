### Title
Signers Cannot Cancel EIP-2612 Permit Signatures Before Deadline - (File: contracts/L2/RsETHTokenWrapper.sol, contracts/agETH/AGETHTokenWrapper.sol, contracts/KERNEL/KERNEL.sol)

### Summary
`RsETHTokenWrapper` (wrsETH), `AGETHTokenWrapper` (wagETH), and `KERNEL` all inherit OpenZeppelin's `ERC20PermitUpgradeable` / `ERC20Permit`, which internally uses the `Nonces` contract. None of these contracts expose any external function to increment a user's nonce, so a signer who has issued a permit signature has no on-chain mechanism to invalidate it before the deadline expires.

### Finding Description
All three token contracts use EIP-2612 `permit()` functionality:

- `RsETHTokenWrapper` inherits `ERC20PermitUpgradeable` [1](#0-0) 
- `AGETHTokenWrapper` inherits `ERC20PermitUpgradeable` [2](#0-1) 
- `KERNEL` inherits `ERC20Permit` [3](#0-2) 

OpenZeppelin's `Nonces` (used internally by both `ERC20Permit` and `ERC20PermitUpgradeable`) only increments a user's nonce when a permit is actually consumed. There is no `useNonce()`, `increaseNonce()`, or equivalent function exposed externally in any of these three contracts. A user who has signed a permit and later wishes to revoke it — for example, because the intended spender's contract was compromised, or because the user changed their mind — has no way to do so on-chain before the deadline. [4](#0-3) 

### Impact Explanation
A user who signs a `permit()` for a spender over their `wrsETH`, `wagETH`, or `KERNEL` tokens cannot cancel that authorization before the deadline. If the signed permit is leaked or the intended spender becomes malicious, the spender can execute the permit at any time up to the deadline, transferring the user's tokens without further consent. This maps to **Low — contract fails to deliver promised returns** (the ability to cancel a signed authorization), with a realistic path to token loss if the permit is misused.

### Likelihood Explanation
EIP-2612 permits are widely used in DeFi integrations (e.g., single-transaction deposit flows). Users routinely sign permits for third-party protocols. If any such protocol is exploited or the signed message is leaked, the user has no recourse. The likelihood is **Medium** given the prevalence of permit-based interactions on L2 networks where these wrapper tokens are deployed.

### Recommendation
Add an external `useNonce()` function (or equivalent `increaseNonce()`) to each affected contract, allowing a signer to self-increment their nonce and thereby invalidate any outstanding permit signatures:

```solidity
/// @notice Allows a signer to invalidate any outstanding permit signatures
function useNonce() external returns (uint256) {
    return _useNonce(msg.sender);
}
```

This is the same fix applied in the referenced Farcaster audit: expose the internal `_useNonce` call so users can cancel their own signatures on demand.

### Proof of Concept
1. Alice signs an EIP-2612 permit granting Bob's DeFi contract allowance over her `wrsETH` balance, with a 1-hour deadline.
2. Bob's DeFi contract is exploited before Alice's permit is consumed.
3. Alice attempts to cancel her permit — there is no `useNonce()` or `increaseNonce()` function on `RsETHTokenWrapper`.
4. The attacker, holding Alice's signed permit message, calls `permit(alice, attacker, amount, deadline, v, r, s)` on `RsETHTokenWrapper`, which succeeds because the nonce is still valid. [5](#0-4) 
5. The attacker then calls `transferFrom(alice, attacker, amount)` to drain Alice's `wrsETH`.
6. The same scenario applies identically to `AGETHTokenWrapper` and `KERNEL`. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L7-20)
```text
    ERC20PermitUpgradeable
} from "@openzeppelin/contracts-upgradeable/token/ERC20/extensions/ERC20PermitUpgradeable.sol";
import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";

import { UtilLib } from "contracts/utils/UtilLib.sol";

/// @title RsETHTokenWrapper
/// @notice This contract is a wrapper for alternative RsETH tokens in L2 chains from a canonical rsETH token for
/// KelpDao
/// @dev it is an upgradeable ERC20 token that wraps an alternative RsETH token
/// It also uses the ERC20PermitUpgradeable extension
/// the alt rsETH tokens can be swapped 1:1 for the canonical rsETH token
contract RsETHTokenWrapper is Initializable, AccessControlUpgradeable, ERC20Upgradeable, ERC20PermitUpgradeable {
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L55-64)
```text
    function initialize(address admin, address bridger, address _altRsETH) external initializer {
        __ERC20_init("rsETHWrapper", "wrsETH");
        __ERC20Permit_init("rsETHWrapper");
        __AccessControl_init();

        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(BRIDGER_ROLE, bridger);

        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L6-17)
```text
import {
    ERC20PermitUpgradeable
} from "@openzeppelin/contracts-upgradeable/token/ERC20/extensions/ERC20PermitUpgradeable.sol";
import { ERC20Upgradeable } from "@openzeppelin/contracts-upgradeable/token/ERC20/ERC20Upgradeable.sol";
import { SafeERC20Upgradeable } from "@openzeppelin/contracts-upgradeable/token/ERC20/utils/SafeERC20Upgradeable.sol";

/// @title AGETHTokenWrapper
/// @notice This contract is a wrapper for alternative agETH tokens in L2 chains for a canonical agETH token from Kelp
/// @dev It is an upgradeable ERC20 token that wraps an alternative agETH token
/// @dev It also uses the ERC20PermitUpgradeable extension
/// @dev The alt agETH tokens can be swapped 1:1 for the canonical agETH token
contract AGETHTokenWrapper is Initializable, AccessControlUpgradeable, ERC20Upgradeable, ERC20PermitUpgradeable {
```

**File:** contracts/KERNEL/KERNEL.sol (L5-11)
```text
import { ERC20Permit } from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";

/// @title KERNEL - Kernel protocol token
contract KERNEL is ERC20, ERC20Permit {
    constructor(address safeAddress) ERC20("KERNEL", "KERNEL") ERC20Permit("KERNEL") {
        _mint(safeAddress, 1_000_000_000 * 10 ** decimals());
    }
```
