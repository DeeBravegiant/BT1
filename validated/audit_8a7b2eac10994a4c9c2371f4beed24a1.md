Looking at the vulnerability class — **reading/using uninitialized state without a guard, causing funds loss instead of a revert** — I need to find an analog in the NEAR MPC codebase where a function accepts a request for a domain whose key has not yet been generated, instead of rejecting it.

Let me examine the `sign()` flow and the `Initializing` state structure.