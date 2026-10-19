// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Script, console} from "forge-std/Script.sol";
import {HufuVault} from "../src/HufuVault.sol";
import {MockStablecoin} from "../src/MockStablecoin.sol";
import {MockFXPool} from "../src/MockFXPool.sol";

/// @notice Deploys the test tokens, FX pool and Ayaan's vault, then seeds the demo state.
///         Reads OWNER_PRIVATE_KEY, AGENT_PRIVATE_KEY and (optional) GUARDIAN_PRIVATE_KEY.
///         Writes deployments/<chainId>.json for the agent and dashboard.
contract Deploy is Script {
    uint256 internal constant UNIT = 1e6; // both stablecoins use 6 decimals

    HufuVault internal vault;
    MockStablecoin internal hkd;
    MockStablecoin internal usd;
    MockFXPool internal pool;
    address internal owner;
    address internal agent;
    address internal guardian;

    /// @dev Payees only receive funds, so they need no keys: derive stable, readable-looking addresses.
    function payeeAddress(string memory name) internal pure returns (address) {
        return address(uint160(uint256(keccak256(abi.encodePacked("hufu.payee.", name)))));
    }

    function run() external {
        uint256 ownerKey = vm.envUint("OWNER_PRIVATE_KEY");
        owner = vm.addr(ownerKey);
        agent = vm.addr(vm.envUint("AGENT_PRIVATE_KEY"));
        uint256 guardianKey = vm.envOr("GUARDIAN_PRIVATE_KEY", uint256(0));
        guardian = guardianKey == 0 ? address(0) : vm.addr(guardianKey);

        address landlord = payeeAddress("landlord");
        address university = payeeAddress("university");
        address roommate = payeeAddress("roommate");
        address phone = payeeAddress("phone");

        vm.startBroadcast(ownerKey);

        hkd = new MockStablecoin("Test HKD Stablecoin", "tHKD");
        usd = new MockStablecoin("Test USD Stablecoin", "tUSD");
        pool = new MockFXPool(usd, hkd, 7_800_000, 10); // sample: 7.80 HKD/USD, 0.10% fee
        vault = new HufuVault(owner, agent, uint64(block.timestamp + 30 days), guardian);

        // The student's signed limits (the ruler's half).
        vault.setTokenPolicy(address(hkd), true, 5_000 * UNIT, 8_000 * UNIT);
        vault.setTokenPolicy(address(usd), true, 500 * UNIT, 1_000 * UNIT);
        vault.setPayee(landlord, true, "Landlord (Mr Chan)");
        vault.setPayee(university, true, "University fees office");
        vault.setPayee(roommate, true, "Roommate (Kevin), utilities split");
        vault.setPayee(phone, true, "Phone & data plan");
        vault.setPayeeCap(roommate, address(hkd), 500 * UNIT);
        vault.setFxPool(address(pool));

        // Balances: HKD savings, plus USD from the family's licensed remittance, on-ramped in HK.
        hkd.mint(address(vault), 10_000 * UNIT);
        usd.mint(address(vault), 800 * UNIT);
        hkd.mint(address(pool), 1_000_000 * UNIT);
        usd.mint(address(pool), 100_000 * UNIT);

        // A mandate signed at onboarding: the phone plan, monthly, first payment due now.
        vault.createMandate(
            phone, address(hkd), 198 * UNIT, 30 days, uint64(block.timestamp), 0, "Phone & data plan"
        );

        // Gas for the other two keys, so only the owner key needs funding (e.g. from a testnet faucet).
        uint256 topUp = vm.envOr("GAS_TOPUP_WEI", uint256(0.01 ether));
        if (agent.balance < topUp) payable(agent).transfer(topUp - agent.balance);
        if (guardian != address(0) && guardian.balance < topUp) {
            payable(guardian).transfer(topUp - guardian.balance);
        }

        vm.stopBroadcast();

        _write();
        console.log("HufuVault:", address(vault));
    }

    function _write() internal {
        string memory p = "payees";
        vm.serializeAddress(p, "landlord", payeeAddress("landlord"));
        vm.serializeAddress(p, "university", payeeAddress("university"));
        vm.serializeAddress(p, "roommate", payeeAddress("roommate"));
        string memory payeesJson = vm.serializeAddress(p, "phone", payeeAddress("phone"));

        string memory d = "deployment";
        vm.serializeUint(d, "chainId", block.chainid);
        vm.serializeUint(d, "deployBlock", block.number);
        vm.serializeAddress(d, "vault", address(vault));
        vm.serializeAddress(d, "tHKD", address(hkd));
        vm.serializeAddress(d, "tUSD", address(usd));
        vm.serializeAddress(d, "fxPool", address(pool));
        vm.serializeAddress(d, "owner", owner);
        vm.serializeAddress(d, "agent", agent);
        vm.serializeAddress(d, "guardian", guardian);
        string memory json = vm.serializeString(d, "payees", payeesJson);

        vm.writeJson(
            json, string.concat(vm.projectRoot(), "/deployments/", vm.toString(block.chainid), ".json")
        );
    }
}
