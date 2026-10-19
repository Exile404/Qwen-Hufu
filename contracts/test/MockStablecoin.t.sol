// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MockStablecoin} from "../src/MockStablecoin.sol";

contract MockStablecoinTest is Test {
    MockStablecoin internal hkd;
    address internal alice = makeAddr("alice");

    function setUp() public {
        hkd = new MockStablecoin("Test HKD Stablecoin", "tHKD");
    }

    function test_DecimalsIsSix() public view {
        assertEq(hkd.decimals(), 6);
    }

    function test_OwnerCanMint() public {
        hkd.mint(alice, 1_000e6);
        assertEq(hkd.balanceOf(alice), 1_000e6);
    }

    function test_RevertWhen_NonOwnerMints() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        hkd.mint(alice, 1e6);
    }
}
