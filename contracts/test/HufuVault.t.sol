// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {HufuVault} from "../src/HufuVault.sol";
import {MockStablecoin} from "../src/MockStablecoin.sol";
import {MockFXPool} from "../src/MockFXPool.sol";

contract HufuVaultTest is Test {
    uint256 internal constant START = 1_792_224_000; // a fixed point in Oct 2026, at 00:00 UTC
    uint256 internal constant HKD = 1e6; // 6 decimals
    uint256 internal constant USD = 1e6;

    HufuVault internal vault;
    MockStablecoin internal hkd;
    MockStablecoin internal usd;
    MockFXPool internal pool;

    address internal owner = makeAddr("owner"); // the student's half
    address internal agent = makeAddr("agent"); // the commander's half
    address internal guardian = makeAddr("guardian");
    address internal stranger = makeAddr("stranger");
    address internal landlord = makeAddr("landlord");
    address internal university = makeAddr("university");
    address internal mum = makeAddr("mum");
    address internal attacker = makeAddr("attacker");

    function setUp() public {
        vm.warp(START);
        hkd = new MockStablecoin("Test HKD Stablecoin", "tHKD");
        usd = new MockStablecoin("Test USD Stablecoin", "tUSD");
        pool = new MockFXPool(usd, hkd, 7_800_000, 10); // 7.80 HKD/USD, 0.10% fee
        vault = new HufuVault(owner, agent, uint64(START + 30 days), guardian);

        vm.startPrank(owner);
        vault.setTokenPolicy(address(hkd), true, 5_000 * HKD, 8_000 * HKD);
        vault.setTokenPolicy(address(usd), true, 500 * USD, 1_000 * USD);
        vault.setPayee(landlord, true, "Landlord (Mr Chan)");
        vault.setPayee(university, true, "University fees office");
        vault.setPayee(mum, true, "Mum (Dhaka)");
        vault.setPayeeCap(mum, address(usd), 200 * USD);
        vault.setFxPool(address(pool));
        vm.stopPrank();

        hkd.mint(address(vault), 20_000 * HKD);
        usd.mint(address(vault), 1_500 * USD);
        hkd.mint(address(pool), 1_000_000 * HKD);
        usd.mint(address(pool), 100_000 * USD);
    }

    // ------------------------------------------------------------------ T1: pay

    function test_Pay_WithinCaps_TransfersAndRecordsSpend() public {
        vm.prank(agent);
        vault.pay(address(hkd), landlord, 4_500 * HKD, keccak256("October rent"));
        assertEq(hkd.balanceOf(landlord), 4_500 * HKD);
        assertEq(hkd.balanceOf(address(vault)), 15_500 * HKD);
        assertEq(vault.spentOnDay(address(hkd), vault.today()), 4_500 * HKD);
        assertEq(vault.remainingToday(address(hkd)), 3_500 * HKD);
    }

    function test_Pay_EmitsPaid() public {
        bytes32 memo = keccak256("groceries");
        vm.expectEmit(true, true, false, true, address(vault));
        emit HufuVault.Paid(address(usd), mum, 100 * USD, memo);
        vm.prank(agent);
        vault.pay(address(usd), mum, 100 * USD, memo);
    }

    function test_RevertWhen_PayCalledByNonAgent() public {
        vm.prank(owner);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));

        vm.prank(stranger);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));
    }

    function test_RevertWhen_PayToUnknownPayee() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.PayeeNotAllowed.selector, attacker));
        vault.pay(address(hkd), attacker, 1 * HKD, bytes32(0));
    }

    function test_RevertWhen_PayOverPerTxCap() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverPerTxCap.selector, 5_001 * HKD, 5_000 * HKD));
        vault.pay(address(hkd), landlord, 5_001 * HKD, bytes32(0));
    }

    function test_RevertWhen_PayOverPayeeCap() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverPayeeCap.selector, 250 * USD, 200 * USD));
        vault.pay(address(usd), mum, 250 * USD, bytes32(0));
    }

    function test_RevertWhen_SplitPaymentsPassPayeeDailyCap() public {
        vm.startPrank(agent);
        vault.pay(address(usd), mum, 150 * USD, bytes32(0));
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverPayeeCap.selector, 100 * USD, 50 * USD));
        vault.pay(address(usd), mum, 100 * USD, bytes32(0));
        vault.pay(address(usd), mum, 50 * USD, bytes32(0));
        vm.stopPrank();
        assertEq(usd.balanceOf(mum), 200 * USD);
        assertEq(vault.payeeRemainingToday(mum, address(usd)), 0);
    }

    function test_PayeeDailyCap_ResetsNextDay() public {
        vm.prank(agent);
        vault.pay(address(usd), mum, 200 * USD, bytes32(0));
        vm.warp(START + 1 days);
        assertEq(vault.payeeRemainingToday(mum, address(usd)), 200 * USD);
        vm.prank(agent);
        vault.pay(address(usd), mum, 200 * USD, bytes32(0));
        assertEq(usd.balanceOf(mum), 400 * USD);
    }

    function test_PayeeRemainingToday_WithoutCapFollowsTokenDailyCap() public {
        vm.prank(agent);
        vault.pay(address(hkd), landlord, 4_500 * HKD, bytes32(0));
        assertEq(vault.payeeRemainingToday(landlord, address(hkd)), 3_500 * HKD);
        assertEq(vault.payeeRemainingToday(university, address(hkd)), 3_500 * HKD);
    }

    function test_RevertWhen_PayOverDailyCap() public {
        vm.startPrank(agent);
        vault.pay(address(hkd), landlord, 4_500 * HKD, bytes32(0));
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverDailyCap.selector, 8_500 * HKD, 8_000 * HKD));
        vault.pay(address(hkd), university, 4_000 * HKD, bytes32(0));
        vm.stopPrank();
    }

    function test_DailyCap_ResetsNextDay() public {
        vm.prank(agent);
        vault.pay(address(hkd), landlord, 5_000 * HKD, bytes32(0));
        vm.warp(START + 1 days);
        assertEq(vault.remainingToday(address(hkd)), 8_000 * HKD);
        vm.prank(agent);
        vault.pay(address(hkd), landlord, 5_000 * HKD, bytes32(0));
        assertEq(hkd.balanceOf(landlord), 10_000 * HKD);
    }

    function test_RevertWhen_PayWithDisabledToken() public {
        MockStablecoin other = new MockStablecoin("Other", "OTH");
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.TokenNotEnabled.selector, address(other)));
        vault.pay(address(other), landlord, 1, bytes32(0));
    }

    function test_RevertWhen_PayZero() public {
        vm.prank(agent);
        vm.expectRevert(HufuVault.ZeroAmount.selector);
        vault.pay(address(hkd), landlord, 0, bytes32(0));
    }

    function test_RevertWhen_PayWhilePaused() public {
        vm.prank(guardian);
        vault.pause();
        vm.prank(agent);
        vm.expectRevert(HufuVault.IsPaused.selector);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));
    }

    function test_RevertWhen_AgentSessionExpired() public {
        vm.warp(START + 30 days + 1);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.AgentExpired.selector, uint64(START + 30 days)));
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));
        assertFalse(vault.agentActive());
    }

    // ------------------------------------------------------------------ T2: requests

    function test_RequestPayment_DoesNotMoveFunds() public {
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), attacker, 9_000 * HKD, keccak256("new bank details"));
        assertEq(id, 0);
        assertEq(hkd.balanceOf(attacker), 0);
        HufuVault.PaymentRequest memory r = vault.getRequest(id);
        assertEq(uint8(r.status), uint8(HufuVault.RequestStatus.Pending));
        assertEq(r.to, attacker);
    }

    function test_RequestPayment_OwnerApprovesOverCap() public {
        address newPayee = makeAddr("dentist");
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), newPayee, 9_000 * HKD, bytes32(0));
        vm.prank(owner);
        vault.approveRequest(id);
        assertEq(hkd.balanceOf(newPayee), 9_000 * HKD);
        assertEq(uint8(vault.getRequest(id).status), uint8(HufuVault.RequestStatus.Approved));
        assertEq(
            vault.remainingToday(address(hkd)), 8_000 * HKD, "owner-approved spend is outside the agent's cap"
        );
    }

    function test_RejectRequest_NoTransfer() public {
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), attacker, 9_000 * HKD, bytes32(0));
        vm.prank(owner);
        vault.rejectRequest(id);
        assertEq(hkd.balanceOf(attacker), 0);
        assertEq(uint8(vault.getRequest(id).status), uint8(HufuVault.RequestStatus.Rejected));
    }

    function test_RevertWhen_ApproveTwice() public {
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), landlord, 100 * HKD, bytes32(0));
        vm.startPrank(owner);
        vault.approveRequest(id);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.RequestNotPending.selector, id));
        vault.approveRequest(id);
        vm.stopPrank();
    }

    function test_RevertWhen_ApproveAfterReject() public {
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), attacker, 100 * HKD, bytes32(0));
        vm.startPrank(owner);
        vault.rejectRequest(id);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.RequestNotPending.selector, id));
        vault.approveRequest(id);
        vm.stopPrank();
    }

    function test_RevertWhen_AgentApprovesOwnRequest() public {
        vm.startPrank(agent);
        uint256 id = vault.requestPayment(address(hkd), attacker, 9_000 * HKD, bytes32(0));
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.approveRequest(id);
        vm.stopPrank();
    }

    function test_RevertWhen_ApproveWhilePaused() public {
        vm.prank(agent);
        uint256 id = vault.requestPayment(address(hkd), landlord, 100 * HKD, bytes32(0));
        vm.startPrank(owner);
        vault.pause();
        vm.expectRevert(HufuVault.IsPaused.selector);
        vault.approveRequest(id);
        vm.stopPrank();
    }

    function test_RevertWhen_ApproveUnknownRequest() public {
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.UnknownRequest.selector, 7));
        vault.approveRequest(7);
    }

    // ------------------------------------------------------------------ mandates

    function _createTuitionMandate() internal returns (uint256 id) {
        vm.prank(owner);
        id = vault.createMandate(
            university,
            address(hkd),
            9_000 * HKD,
            30 days,
            uint64(START),
            uint64(START + 240 days),
            "Tuition instalment"
        );
    }

    function test_Mandate_ExecutesWhenDueAndAdvances() public {
        uint256 id = _createTuitionMandate();
        vm.prank(agent);
        vault.executeMandate(id);
        assertEq(hkd.balanceOf(university), 9_000 * HKD);
        assertEq(vault.getMandate(id).nextDue, uint64(START + 30 days));
    }

    function test_Mandate_IsOutsideTheDailyCap() public {
        uint256 id = _createTuitionMandate();
        vm.prank(agent);
        vault.executeMandate(id); // 9,000 > 8,000 daily cap, allowed because the owner signed it
        assertEq(vault.remainingToday(address(hkd)), 8_000 * HKD);
    }

    function test_RevertWhen_MandateExecutedTwiceInOnePeriod() public {
        uint256 id = _createTuitionMandate();
        vm.startPrank(agent);
        vault.executeMandate(id);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.MandateNotDue.selector, id, uint64(START + 30 days)));
        vault.executeMandate(id);
        vm.stopPrank();
    }

    function test_RevertWhen_MandateEnded() public {
        vm.prank(owner);
        uint256 id = vault.createMandate(
            university, address(hkd), 1_000 * HKD, 30 days, uint64(START), uint64(START + 10 days), "Short"
        );
        vm.startPrank(agent);
        vault.executeMandate(id);
        vm.warp(START + 30 days);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.MandateEnded.selector, id));
        vault.executeMandate(id);
        vm.stopPrank();
    }

    function test_OneOffMandate_DeactivatesAfterPayment() public {
        vm.prank(owner);
        uint256 id = vault.createMandate(landlord, address(hkd), 2_000 * HKD, 0, uint64(START), 0, "Deposit");
        vm.startPrank(agent);
        vault.executeMandate(id);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.MandateNotActive.selector, id));
        vault.executeMandate(id);
        vm.stopPrank();
    }

    function test_RevertWhen_MandateCancelled() public {
        uint256 id = _createTuitionMandate();
        vm.prank(owner);
        vault.cancelMandate(id);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.MandateNotActive.selector, id));
        vault.executeMandate(id);
    }

    function test_RevertWhen_MandatePayeeRevoked() public {
        uint256 id = _createTuitionMandate();
        vm.prank(owner);
        vault.setPayee(university, false, "revoked");
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.PayeeNotAllowed.selector, university));
        vault.executeMandate(id);
    }

    function test_RevertWhen_MandateForUnknownPayee() public {
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.PayeeNotAllowed.selector, attacker));
        vault.createMandate(attacker, address(hkd), 1 * HKD, 1 days, uint64(START), 0, "x");
    }

    function test_RevertWhen_UnknownMandate() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.UnknownMandate.selector, 3));
        vault.executeMandate(3);
    }

    // ------------------------------------------------------------------ swaps

    function test_Swap_UsdToHkd_AtRateMinusFee() public {
        vm.prank(agent);
        uint256 out = vault.swap(address(usd), 100 * USD, 0);
        assertEq(out, 779_220_000); // 100 * 7.80 * (1 - 0.001) = 779.22 tHKD
        assertEq(hkd.balanceOf(address(vault)), 20_000 * HKD + out);
        assertEq(usd.balanceOf(address(vault)), 1_400 * USD);
        assertEq(usd.allowance(address(vault), address(pool)), 0);
    }

    function test_RevertWhen_SwapSlippage() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(MockFXPool.Slippage.selector, 779_220_000, 780 * HKD));
        vault.swap(address(usd), 100 * USD, 780 * HKD);
    }

    function test_RevertWhen_SwapOverCap() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverPerTxCap.selector, 600 * USD, 500 * USD));
        vault.swap(address(usd), 600 * USD, 0);
    }

    function test_Swap_CountsTowardDailyCap() public {
        vm.startPrank(agent);
        vault.swap(address(usd), 500 * USD, 0);
        vault.swap(address(usd), 500 * USD, 0);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverDailyCap.selector, 1_001 * USD, 1_000 * USD));
        vault.swap(address(usd), 1 * USD, 0);
        vm.stopPrank();
    }

    function test_RevertWhen_NoFxPool() public {
        vm.prank(owner);
        vault.setFxPool(address(0));
        vm.prank(agent);
        vm.expectRevert(HufuVault.NoFxPool.selector);
        vault.swap(address(usd), 1 * USD, 0);
    }

    // ------------------------------------------------------------------ pause: the beacon-fire rule

    function test_AnyRoleCanPause() public {
        address[3] memory roles = [agent, guardian, owner];
        for (uint256 i; i < roles.length; ++i) {
            vm.prank(roles[i]);
            vault.pause();
            assertTrue(vault.paused());
            vm.prank(owner);
            vault.unpause();
        }
    }

    function test_RevertWhen_StrangerPauses() public {
        vm.prank(stranger);
        vm.expectRevert(HufuVault.NotAllowedToPause.selector);
        vault.pause();
    }

    function test_RevertWhen_AgentOrGuardianUnpauses() public {
        vm.prank(guardian);
        vault.pause();
        vm.prank(agent);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.unpause();
        vm.prank(guardian);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.unpause();
    }

    function test_OwnerCanWithdrawWhilePaused() public {
        vm.startPrank(owner);
        vault.pause();
        vault.withdraw(address(hkd), owner, 20_000 * HKD);
        vm.stopPrank();
        assertEq(hkd.balanceOf(owner), 20_000 * HKD);
    }

    // ------------------------------------------------------------------ T3: no path to policy

    function test_AgentCannotChangeAnyPolicy() public {
        vm.startPrank(agent);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setTokenPolicy(address(hkd), true, type(uint256).max, type(uint256).max);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setPayee(attacker, true, "totally the landlord");
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setPayeeCap(mum, address(usd), type(uint256).max);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setAgent(agent, type(uint64).max);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setGuardian(attacker);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.setFxPool(attacker);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.createMandate(landlord, address(hkd), 1, 1, uint64(START), 0, "x");
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.cancelMandate(0);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.withdraw(address(hkd), attacker, 1);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.transferOwnership(attacker);
        vm.stopPrank();
    }

    function test_TransferOwnership() public {
        address phone2 = makeAddr("new phone");
        vm.prank(owner);
        vault.transferOwnership(phone2);
        assertEq(vault.owner(), phone2);
        vm.prank(owner);
        vm.expectRevert(HufuVault.NotOwner.selector);
        vault.unpause();
    }

    // ------------------------------------------------------------------ key rotation (risk R2)

    function test_SetAgent_RotatesTheSessionKey() public {
        address newAgent = makeAddr("new agent");
        vm.expectEmit(true, false, false, true, address(vault));
        emit HufuVault.AgentSet(newAgent, uint64(START + 7 days));
        vm.prank(owner);
        vault.setAgent(newAgent, uint64(START + 7 days));

        vm.prank(agent);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));

        vm.prank(newAgent);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));
        assertEq(hkd.balanceOf(landlord), 1 * HKD);
    }

    function test_SetAgentToZero_RevokesTheAgent() public {
        vm.prank(owner);
        vault.setAgent(address(0), 0);
        assertFalse(vault.agentActive());
        vm.prank(agent);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.pay(address(hkd), landlord, 1 * HKD, bytes32(0));
    }

    function test_SetGuardian_RotatesWhoCanPause() public {
        address newGuardian = makeAddr("new guardian");
        vm.prank(owner);
        vault.setGuardian(newGuardian);
        vm.prank(guardian);
        vm.expectRevert(HufuVault.NotAllowedToPause.selector);
        vault.pause();
        vm.prank(newGuardian);
        vault.pause();
        assertTrue(vault.paused());
    }

    // ------------------------------------------------------------------ input validation and edge cases

    function test_RevertWhen_ConstructedWithoutOwner() public {
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        new HufuVault(address(0), agent, uint64(START + 30 days), guardian);
    }

    function test_RevertWhen_RequestPaymentInvalid() public {
        vm.startPrank(agent);
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        vault.requestPayment(address(hkd), address(0), 1 * HKD, bytes32(0));
        vm.expectRevert(HufuVault.ZeroAmount.selector);
        vault.requestPayment(address(hkd), landlord, 0, bytes32(0));
        vm.expectRevert(abi.encodeWithSelector(HufuVault.TokenNotEnabled.selector, attacker));
        vault.requestPayment(attacker, landlord, 1, bytes32(0));
        vm.stopPrank();
        assertEq(vault.requestCount(), 0);
    }

    function test_RevertWhen_CreateOrCancelMandateInvalid() public {
        vm.startPrank(owner);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.TokenNotEnabled.selector, attacker));
        vault.createMandate(university, attacker, 1, 30 days, uint64(START), 0, "x");
        vm.expectRevert(HufuVault.ZeroAmount.selector);
        vault.createMandate(university, address(hkd), 0, 30 days, uint64(START), 0, "x");
        vm.expectRevert(abi.encodeWithSelector(HufuVault.UnknownMandate.selector, 0));
        vault.cancelMandate(0);
        vm.stopPrank();
        assertEq(vault.mandateCount(), 0);
    }

    function test_RevertWhen_MandateTokenDisabledLater() public {
        uint256 id = _createTuitionMandate();
        vm.prank(owner);
        vault.setTokenPolicy(address(hkd), false, 0, 0);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.TokenNotEnabled.selector, address(hkd)));
        vault.executeMandate(id);
    }

    function test_RevertWhen_SwapIntoDisabledToken() public {
        vm.prank(owner);
        vault.setTokenPolicy(address(hkd), false, 0, 0);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.TokenNotEnabled.selector, address(hkd)));
        vault.swap(address(usd), 1 * USD, 0);
    }

    function test_RevertWhen_OwnerPassesZeroAddress() public {
        vm.startPrank(owner);
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        vault.setTokenPolicy(address(0), true, 1, 1);
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        vault.setPayee(address(0), true, "nobody");
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        vault.withdraw(address(hkd), address(0), 1);
        vm.expectRevert(HufuVault.ZeroAddress.selector);
        vault.transferOwnership(address(0));
        vm.stopPrank();
    }

    function test_RevertWhen_ReadingUnknownIds() public {
        vm.expectRevert(abi.encodeWithSelector(HufuVault.UnknownMandate.selector, 3));
        vault.getMandate(3);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.UnknownRequest.selector, 3));
        vault.getRequest(3);
    }

    function test_RemainingToday_IsZeroWhenDailyCapLoweredBelowSpend() public {
        vm.prank(agent);
        vault.pay(address(hkd), landlord, 4_500 * HKD, bytes32(0));
        vm.prank(owner);
        vault.setTokenPolicy(address(hkd), true, 5_000 * HKD, 1_000 * HKD);
        assertEq(vault.remainingToday(address(hkd)), 0);
    }

    function test_PayeeRemainingToday_NeverExceedsTokenDailyCap() public {
        vm.startPrank(agent);
        vault.pay(address(usd), landlord, 500 * USD, bytes32(0));
        vault.pay(address(usd), landlord, 400 * USD, bytes32(0));
        vm.stopPrank();
        assertEq(vault.payeeRemainingToday(mum, address(usd)), 100 * USD); // mum's own cap would allow 200
    }

    function test_RevertWhen_PayeeCapLoweredBelowTodaysPayments() public {
        vm.prank(agent);
        vault.pay(address(usd), mum, 200 * USD, bytes32(0));
        vm.prank(owner);
        vault.setPayeeCap(mum, address(usd), 100 * USD);
        assertEq(vault.payeeRemainingToday(mum, address(usd)), 0);
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.OverPayeeCap.selector, 1, 0));
        vault.pay(address(usd), mum, 1, bytes32(0));
    }

    // ------------------------------------------------------------------ audit anchoring

    function test_AnchorAudit_Emits() public {
        bytes32 head = keccak256("audit head");
        vm.expectEmit(true, false, false, true, address(vault));
        emit HufuVault.AuditAnchored(head, START);
        vm.prank(agent);
        vault.anchorAudit(head);
    }

    function test_RevertWhen_NonAgentAnchors() public {
        vm.prank(stranger);
        vm.expectRevert(HufuVault.NotAgent.selector);
        vault.anchorAudit(bytes32(0));
    }

    // ------------------------------------------------------------------ the live-jailbreak scenario

    /// A fully compromised agent key tries everything it can in one day.
    /// Nothing reaches the attacker, and total outflow is bounded by the daily caps,
    /// paid only to payees the student already trusts.
    function test_JailbrokenAgent_LossBoundedByDailyCaps() public {
        uint256 hkdBefore = hkd.balanceOf(address(vault));
        uint256 usdBefore = usd.balanceOf(address(vault));

        vm.startPrank(agent);
        vm.expectRevert(abi.encodeWithSelector(HufuVault.PayeeNotAllowed.selector, attacker));
        vault.pay(address(hkd), attacker, 20_000 * HKD, bytes32(0));
        vault.requestPayment(address(hkd), attacker, 20_000 * HKD, bytes32(0)); // only queues
        for (uint256 i; i < 10; ++i) {
            try vault.pay(address(hkd), landlord, 5_000 * HKD, bytes32(0)) {} catch {}
            try vault.pay(address(usd), mum, 200 * USD, bytes32(0)) {} catch {}
        }
        vm.stopPrank();

        assertEq(hkd.balanceOf(attacker), 0);
        assertEq(usd.balanceOf(attacker), 0);
        assertLe(hkdBefore - hkd.balanceOf(address(vault)), 8_000 * HKD);
        assertLe(usdBefore - usd.balanceOf(address(vault)), 1_000 * USD);
    }

    // ------------------------------------------------------------------ fuzz

    function testFuzz_AgentNeverExceedsDailyCap(uint256[8] memory amounts) public {
        uint256 paid;
        for (uint256 i; i < amounts.length; ++i) {
            uint256 amount = bound(amounts[i], 1, 6_000 * HKD);
            vm.prank(agent);
            try vault.pay(address(hkd), landlord, amount, bytes32(0)) {
                paid += amount;
            } catch {}
        }
        assertLe(paid, 8_000 * HKD);
        assertEq(hkd.balanceOf(landlord), paid);
    }

    function testFuzz_PayeeCapHolds(uint256 amount) public {
        amount = bound(amount, 1, 500 * USD);
        vm.prank(agent);
        try vault.pay(address(usd), mum, amount, bytes32(0)) {
            assertLe(amount, 200 * USD);
        } catch {
            assertGt(amount, 200 * USD);
        }
    }

    function testFuzz_PayeeDailyCapHoldsAcrossSplits(uint256 a, uint256 b, uint256 c) public {
        uint256[3] memory parts = [bound(a, 1, 300 * USD), bound(b, 1, 300 * USD), bound(c, 1, 300 * USD)];
        vm.startPrank(agent);
        for (uint256 i; i < parts.length; ++i) {
            try vault.pay(address(usd), mum, parts[i], bytes32(0)) {} catch {}
        }
        vm.stopPrank();
        assertLe(usd.balanceOf(mum), 200 * USD);
    }
}
