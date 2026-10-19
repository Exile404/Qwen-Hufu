// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {Base64} from "@openzeppelin/contracts/utils/Base64.sol";
import {WebAuthn} from "@openzeppelin/contracts/utils/cryptography/WebAuthn.sol";
import {HufuVault} from "../src/HufuVault.sol";
import {MockStablecoin} from "../src/MockStablecoin.sol";

/// @notice The owner's half as a passkey: WebAuthn assertions built exactly as a browser does, signed
///         with Foundry's P-256 cheatcode, verified by the vault.
contract HufuVaultPasskeyTest is Test {
    uint256 internal constant START = 1_792_224_000;
    uint256 internal constant HKD = 1e6;
    uint256 internal constant P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551;
    uint256 internal constant PASSKEY = 0xA11CE; // the student's passkey (private half stays on the device)
    uint256 internal constant OTHER_KEY = 0xBAD;
    bytes1 internal constant UP_UV = 0x05; // user present + user verified (fingerprint / face / PIN)

    HufuVault internal vault;
    MockStablecoin internal hkd;

    address internal owner = makeAddr("owner");
    address internal agent = makeAddr("agent");
    address internal guardian = makeAddr("guardian");
    address internal stranger = makeAddr("stranger");
    address internal landlord = makeAddr("landlord");
    address internal attacker = makeAddr("attacker");

    bytes internal constant CREDENTIAL_ID = hex"5c1d0e7a";

    function setUp() public {
        vm.warp(START);
        hkd = new MockStablecoin("Test HKD Stablecoin", "tHKD");
        vault = new HufuVault(owner, agent, uint64(START + 30 days), guardian);
        vm.startPrank(owner);
        vault.setTokenPolicy(address(hkd), true, 5_000 * HKD, 8_000 * HKD);
        vault.setPayee(landlord, true, "Landlord (Mr Chan)");
        vm.stopPrank();
        hkd.mint(address(vault), 20_000 * HKD);
    }

    // ------------------------------------------------------------------ helpers

    function _register(uint256 key) internal {
        (uint256 x, uint256 y) = vm.publicKeyP256(key);
        vm.prank(owner);
        vault.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
    }

    /// @dev A WebAuthn assertion as the browser returns it: authenticatorData, clientDataJSON with the
    ///      challenge in base64url, and a low-s P-256 signature over authData || sha256(clientDataJSON).
    function _assert(uint256 key, bytes32 challenge, bytes1 flags)
        internal
        pure
        returns (WebAuthn.WebAuthnAuth memory auth)
    {
        auth.authenticatorData = abi.encodePacked(sha256("localhost"), flags, uint32(0));
        auth.clientDataJSON = string.concat(
            '{"type":"webauthn.get","challenge":"',
            Base64.encodeURL(abi.encodePacked(challenge)),
            '","origin":"http://localhost:3000","crossOrigin":false}'
        );
        auth.typeIndex = 1;
        auth.challengeIndex = 23;
        bytes32 h = sha256(abi.encodePacked(auth.authenticatorData, sha256(bytes(auth.clientDataJSON))));
        (bytes32 r, bytes32 s) = vm.signP256(key, h);
        if (uint256(s) > P256_N / 2) s = bytes32(P256_N - uint256(s));
        auth.r = r;
        auth.s = s;
    }

    function _sign(bytes memory data, uint64 validUntil)
        internal
        view
        returns (WebAuthn.WebAuthnAuth memory)
    {
        return _assert(PASSKEY, vault.passkeyDigest(data, vault.passkeyNonce(), validUntil), UP_UV);
    }

    function _deadline() internal view returns (uint64) {
        return uint64(vm.getBlockTimestamp() + 5 minutes);
    }

    function _request(uint256 amount) internal returns (uint256 id) {
        vm.prank(agent);
        id = vault.requestPayment(address(hkd), landlord, amount, keccak256("deposit"));
    }

    function _approveCall(uint256 id) internal pure returns (bytes memory) {
        return abi.encodeCall(HufuVault.approveRequest, (id));
    }

    // ------------------------------------------------------------------ registering

    function test_SetPasskey_StoresKeyAndLogsCredential() public {
        (uint256 x, uint256 y) = vm.publicKeyP256(PASSKEY);
        vm.expectEmit(false, false, false, true, address(vault));
        emit HufuVault.PasskeySet(bytes32(x), bytes32(y), CREDENTIAL_ID);
        vm.prank(owner);
        vault.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
        assertEq(vault.passkeyX(), bytes32(x));
        assertEq(vault.passkeyY(), bytes32(y));
    }

    function test_RevertWhen_SetPasskeyNotOnCurve() public {
        vm.prank(owner);
        vm.expectRevert(HufuVault.InvalidPasskey.selector);
        vault.setPasskey(bytes32(uint256(1)), bytes32(uint256(2)), CREDENTIAL_ID);
    }

    function test_RevertWhen_SetPasskeyByAgentOrStranger() public {
        (uint256 x, uint256 y) = vm.publicKeyP256(OTHER_KEY);
        vm.prank(agent);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
        vm.prank(stranger);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
    }

    function test_RevertWhen_NoPasskeyRegistered() public {
        uint256 id = _request(6_000 * HKD);
        WebAuthn.WebAuthnAuth memory auth =
            _assert(PASSKEY, vault.passkeyDigest(_approveCall(id), 0, _deadline()), UP_UV);
        vm.expectRevert(HufuVault.NoPasskey.selector);
        vault.executeWithPasskey(_approveCall(id), _deadline(), auth);
    }

    // ------------------------------------------------------------------ signing an approval

    function test_PasskeyApprovesRequest_RelayedByAgent() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD); // over the agent's per-payment limit: T2
        bytes memory data = _approveCall(id);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(data, deadline);

        vm.expectEmit(true, true, false, true, address(vault));
        emit HufuVault.PasskeyUsed(0, HufuVault.approveRequest.selector);
        vm.prank(agent); // the agent carries the student's half to the vault, but cannot forge it
        vault.executeWithPasskey(data, deadline, auth);

        assertEq(hkd.balanceOf(landlord), 6_000 * HKD);
        assertEq(vault.passkeyNonce(), 1);
        assertEq(uint8(vault.getRequest(id).status), uint8(HufuVault.RequestStatus.Approved));
    }

    function test_RevertWhen_PasskeySignatureReplayed() public {
        _register(PASSKEY);
        uint256 a = _request(6_000 * HKD);
        uint256 b = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(a), deadline);
        vault.executeWithPasskey(_approveCall(a), deadline, auth);

        // The same signature again (nonce moved on), or used for another request: both fail.
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(a), deadline, auth);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(b), deadline, auth);
    }

    function test_RevertWhen_SignedCallIsSwapped() public {
        _register(PASSKEY);
        _request(6_000 * HKD);
        uint64 deadline = _deadline();
        // The student signed "approve #0"; a compromised relayer submits "withdraw everything" instead.
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(0), deadline);
        bytes memory swapped = abi.encodeCall(HufuVault.withdraw, (address(hkd), attacker, 20_000 * HKD));
        vm.prank(attacker);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(swapped, deadline, auth);
        assertEq(hkd.balanceOf(attacker), 0);
    }

    function test_RevertWhen_DeadlineChangedAfterSigning() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(id), deadline);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(id), deadline + 1 days, auth);
    }

    function test_RevertWhen_PasskeyApprovalExpired() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(id), deadline);
        vm.warp(deadline + 1);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.PasskeyExpired.selector, deadline));
        vault.executeWithPasskey(_approveCall(id), deadline, auth);
    }

    function test_RevertWhen_SignedByAnotherPasskey() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth =
            _assert(OTHER_KEY, vault.passkeyDigest(_approveCall(id), 0, deadline), UP_UV);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(id), deadline, auth);
    }

    function test_RevertWhen_UserNotVerified() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        bytes32 digest = vault.passkeyDigest(_approveCall(id), 0, deadline);
        // Touched but not verified (no fingerprint/PIN): refused.
        WebAuthn.WebAuthnAuth memory presentOnly = _assert(PASSKEY, digest, 0x01);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(id), deadline, presentOnly);
        // Not even touched: refused.
        WebAuthn.WebAuthnAuth memory silent = _assert(PASSKEY, digest, 0x04);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(id), deadline, silent);
    }

    function test_RevertWhen_HighSSignature() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(id), deadline);
        auth.s = bytes32(P256_N - uint256(auth.s)); // the malleable twin of a valid signature
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(_approveCall(id), deadline, auth);
    }

    function test_RevertWhen_SignatureFromAnotherVault() public {
        _register(PASSKEY);
        HufuVault twin = new HufuVault(owner, agent, uint64(START + 30 days), guardian);
        (uint256 x, uint256 y) = vm.publicKeyP256(PASSKEY);
        vm.prank(owner);
        twin.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        bytes memory data = _approveCall(id);
        WebAuthn.WebAuthnAuth memory forTwin = _assert(PASSKEY, twin.passkeyDigest(data, 0, deadline), UP_UV);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(data, deadline, forTwin);
    }

    function test_RevertWhen_PasskeyCallIsEmpty() public {
        _register(PASSKEY);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(hex"", deadline);
        vm.expectRevert(HufuVault.NoCall.selector);
        vault.executeWithPasskey(hex"", deadline, auth);
    }

    function test_PasskeyCall_PassesTheVaultErrorThrough() public {
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        vm.prank(owner);
        vault.rejectRequest(id);
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(id), deadline);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.RequestNotPending.selector, id));
        vault.executeWithPasskey(_approveCall(id), deadline, auth);
        assertEq(vault.passkeyNonce(), 0); // the whole thing rolled back
    }

    function testFuzz_RevertWhen_AnyByteOfTheCallChanges(uint256 index, bytes1 flip) public {
        vm.assume(flip != 0);
        _register(PASSKEY);
        uint256 id = _request(6_000 * HKD);
        uint64 deadline = _deadline();
        bytes memory data = _approveCall(id);
        WebAuthn.WebAuthnAuth memory auth = _sign(data, deadline);
        index = bound(index, 0, data.length - 1);
        data[index] ^= flip;
        vm.expectRevert(); // BadPasskeySignature, or NoCall if the selector itself was changed
        vault.executeWithPasskey(data, deadline, auth);
    }

    // ------------------------------------------------------------------ retiring the owner key

    function test_RevertWhen_OwnerKeyRetiredDirectly() public {
        _register(PASSKEY);
        vm.prank(owner);
        vm.expectRevert(HufuVault.OnlyViaPasskey.selector);
        vault.setOwnerKeyRetired(true);
    }

    function test_RetiredOwnerKey_HasNoPowerLeft_PasskeyStillWorks() public {
        _register(PASSKEY);
        bytes memory retire = abi.encodeCall(HufuVault.setOwnerKeyRetired, (true));
        uint64 deadline = _deadline();
        vm.expectEmit(false, false, false, true, address(vault));
        emit HufuVault.OwnerKeyRetired(true);
        vault.executeWithPasskey(retire, deadline, _sign(retire, deadline));
        assertTrue(vault.ownerKeyRetired());

        // A stolen owner key can no longer approve, withdraw, change limits or swap the passkey.
        uint256 id = _request(6_000 * HKD);
        vm.startPrank(owner);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.approveRequest(id);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.withdraw(address(hkd), attacker, 1 * HKD);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setTokenPolicy(address(hkd), true, 1e12, 1e12);
        (uint256 x, uint256 y) = vm.publicKeyP256(OTHER_KEY);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setPasskey(bytes32(x), bytes32(y), CREDENTIAL_ID);
        vm.stopPrank();

        // The passkey still can.
        bytes memory approve = _approveCall(id);
        vault.executeWithPasskey(approve, deadline, _sign(approve, deadline));
        assertEq(hkd.balanceOf(landlord), 6_000 * HKD);
    }

    function test_RetiredOwnerKey_CanStillFreeze_PasskeyUnfreezes() public {
        _register(PASSKEY);
        bytes memory retire = abi.encodeCall(HufuVault.setOwnerKeyRetired, (true));
        uint64 deadline = _deadline();
        vault.executeWithPasskey(retire, deadline, _sign(retire, deadline));

        vm.prank(owner); // the beacon-fire rule: stopping never needs the other half
        vault.pause();
        vm.prank(owner);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.unpause();

        bytes memory unpause = abi.encodeCall(HufuVault.unpause, ());
        vault.executeWithPasskey(unpause, deadline, _sign(unpause, deadline));
        assertFalse(vault.paused());
    }

    function test_Passkey_CanUnlockOwnerKeyAgain() public {
        _register(PASSKEY);
        uint64 deadline = _deadline();
        bytes memory retire = abi.encodeCall(HufuVault.setOwnerKeyRetired, (true));
        vault.executeWithPasskey(retire, deadline, _sign(retire, deadline));
        bytes memory restore = abi.encodeCall(HufuVault.setOwnerKeyRetired, (false));
        vault.executeWithPasskey(restore, deadline, _sign(restore, deadline));
        assertFalse(vault.ownerKeyRetired());
        vm.prank(owner);
        vault.setPayee(attacker, false, "blocked");
    }

    function test_Passkey_RotatesItself() public {
        _register(PASSKEY);
        (uint256 x, uint256 y) = vm.publicKeyP256(OTHER_KEY);
        bytes memory rotate = abi.encodeCall(HufuVault.setPasskey, (bytes32(x), bytes32(y), hex"0e"));
        uint64 deadline = _deadline();
        vault.executeWithPasskey(rotate, deadline, _sign(rotate, deadline));
        assertEq(vault.passkeyX(), bytes32(x));

        // The old passkey is now useless; the new one works.
        uint256 id = _request(6_000 * HKD);
        bytes memory approve = _approveCall(id);
        WebAuthn.WebAuthnAuth memory old = _sign(approve, deadline);
        vm.expectRevert(HufuVault.BadPasskeySignature.selector);
        vault.executeWithPasskey(approve, deadline, old);
        WebAuthn.WebAuthnAuth memory fresh =
            _assert(OTHER_KEY, vault.passkeyDigest(approve, vault.passkeyNonce(), deadline), UP_UV);
        vault.executeWithPasskey(approve, deadline, fresh);
        assertEq(hkd.balanceOf(landlord), 6_000 * HKD);
    }

    function test_RemovePasskey_BackToOwnerKeyAlone() public {
        _register(PASSKEY);
        bytes memory remove = abi.encodeCall(HufuVault.removePasskey, ());
        uint64 deadline = _deadline();
        vm.expectEmit(false, false, false, true, address(vault));
        emit HufuVault.PasskeySet(0, 0, "");
        vault.executeWithPasskey(remove, deadline, _sign(remove, deadline));
        assertEq(vault.passkeyX(), bytes32(0));

        uint256 id = _request(6_000 * HKD);
        WebAuthn.WebAuthnAuth memory auth = _sign(_approveCall(id), deadline);
        vm.expectRevert(HufuVault.NoPasskey.selector);
        vault.executeWithPasskey(_approveCall(id), deadline, auth);
        vm.prank(owner);
        vault.approveRequest(id);
        assertEq(hkd.balanceOf(landlord), 6_000 * HKD);
    }

    function test_RevertWhen_RemovingPasskeyWhileOwnerKeyLocked() public {
        _register(PASSKEY);
        uint64 deadline = _deadline();
        bytes memory retire = abi.encodeCall(HufuVault.setOwnerKeyRetired, (true));
        vault.executeWithPasskey(retire, deadline, _sign(retire, deadline));
        bytes memory remove = abi.encodeCall(HufuVault.removePasskey, ());
        WebAuthn.WebAuthnAuth memory auth = _sign(remove, deadline);
        vm.expectRevert(HufuVault.OwnerKeyLocked.selector);
        vault.executeWithPasskey(remove, deadline, auth);
        assertTrue(vault.passkeyX() != bytes32(0));
    }

    function test_Passkey_CannotActAsAgent() public {
        _register(PASSKEY);
        bytes memory pay = abi.encodeCall(HufuVault.pay, (address(hkd), landlord, 1 * HKD, bytes32(0)));
        uint64 deadline = _deadline();
        WebAuthn.WebAuthnAuth memory auth = _sign(pay, deadline);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.executeWithPasskey(pay, deadline, auth);
    }
}
