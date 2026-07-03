import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 30
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "jito-foundation/jito-solana"
# todo: the name of the repository
REPO_NAME = "jito-solana"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
scope_files = [
    "eras/allegra/impl/src/Cardano/Ledger/Allegra.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/BlockBody.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Core.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Era.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Forecast.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/PParams.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Bbody.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Deleg.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Delegs.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Delpl.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Ledger.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Ledgers.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Pool.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Ppup.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Utxo.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Rules/Utxow.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/State.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/State/Account.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/State/CertState.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/State/Stake.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Transition.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Translation.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/Tx.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/TxAuxData.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/TxBody.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/TxCert.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/TxOut.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/TxWits.hs",
    "eras/allegra/impl/src/Cardano/Ledger/Allegra/UTxO.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/BlockBody.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/BlockBody/Internal.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Core.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Era.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Forecast.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Genesis.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/PParams.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/Context.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/Evaluate.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/TxInfo.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Deleg.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Delegs.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Delpl.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Ledger.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Ledgers.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Pool.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Ppup.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxos.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxow.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Scripts.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/State.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/State/Account.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/State/CertState.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/State/Stake.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Transition.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Translation.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxAuxData.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxBody.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxCert.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxOut.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxWits.hs",
    "eras/alonzo/impl/src/Cardano/Ledger/Alonzo/UTxO.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/BlockBody.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Collateral.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Core.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Era.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Forecast.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/PParams.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Bbody.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Deleg.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Delegs.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Delpl.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Ledger.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Ledgers.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Pool.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Ppup.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Utxo.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Utxos.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Rules/Utxow.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Scripts.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/State.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/State/Account.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/State/CertState.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/State/Stake.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Transition.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Translation.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/Tx.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxAuxData.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxBody.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxCert.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxInfo.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxOut.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/TxWits.hs",
    "eras/babbage/impl/src/Cardano/Ledger/Babbage/UTxO.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Block.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Body.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Boundary.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Header.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Proof.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/Validation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Block/ValidationMode.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Byron/API.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Byron/API/Common.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Byron/API/Mempool.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Byron/API/Protocol.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Byron/API/Validation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/AddrAttributes.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/AddrSpendingData.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/Address.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/AddressHash.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/Attributes.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/BlockCount.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/CBOR.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/ChainDifficulty.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/Compact.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/KeyHash.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/Lovelace.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/LovelacePortion.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/Merkle.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/NetworkMagic.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/TxFeePolicy.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Common/TxSizeLinear.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Constants.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Certificate.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Map.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Payload.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Validation/Activation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Validation/Interface.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Delegation/Validation/Scheduling.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Epoch/File.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Epoch/Validation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/AvvmBalances.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Config.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Data.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Delegation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Generate.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Hash.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Initializer.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/KeyHashes.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/NonAvvmBalances.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Genesis/Spec.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/MempoolPayload.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/ProtocolConstants.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting/EpochAndSlotCount.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting/EpochNumber.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting/EpochSlots.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting/SlotCount.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Slotting/SlotNumber.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Ssc.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Compact.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/GenesisUTxO.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Tx.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/TxAux.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/TxPayload.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/TxProof.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/TxWitness.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/UTxO.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/UTxOConfiguration.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/UTxO/ValidationMode.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/ApplicationName.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/InstallerHash.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Payload.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Proof.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Proposal.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/ProtocolParameters.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/ProtocolParametersUpdate.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/ProtocolVersion.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/SoftforkRule.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/SoftwareVersion.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/SystemTag.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Validation/Endorsement.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Validation/Interface.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Validation/Interface/ProtocolVersionBump.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Validation/Registration.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Validation/Voting.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/Update/Vote.hs",
    "eras/byron/ledger/impl/src/Cardano/Chain/ValidationMode.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/BlockBody.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Core.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Era.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Forecast.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Genesis.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Governance.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Procedures.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Proposals.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Cert.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/HardFork.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledgers.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Mempool.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/NewEpoch.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Pool.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Tickf.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Utxo.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Utxos.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Utxow.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Scripts.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/State.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/State/Account.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/State/CertState.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/State/Stake.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Transition.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Translation.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxAuxData.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxBody.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxInfo.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxOut.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/TxWits.hs",
    "eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/BlockBody.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/BlockBody/Internal.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Core.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Era.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Forecast.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Genesis.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Governance.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Cert.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Certs.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Deleg.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Gov.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/GovCert.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledgers.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Mempool.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Pool.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubCert.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubCerts.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubDeleg.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubGov.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubGovCert.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubPool.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxow.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxos.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/State.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/State/Account.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/State/CertState.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/State/Stake.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Transition.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Translation.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Tx.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxAuxData.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxOut.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxWits.hs",
    "eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/BlockBody.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Core.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Era.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Forecast.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/PParams.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Bbody.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Deleg.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Delegs.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Delpl.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Ledger.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Ledgers.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Pool.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Ppup.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Utxo.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Rules/Utxow.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Scripts.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/State.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/State/Account.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/State/CertState.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/State/Stake.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Transition.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Translation.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Tx.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/TxAuxData.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/TxBody.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/TxCert.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/TxOut.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/TxWits.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs",
    "eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Chain.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/ByronTranslation.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/Forecast.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/Mempool.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/Types.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/Validation.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/API/Wallet.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/AdaPots.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/BlockBody.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/BlockBody/Internal.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Core.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Era.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Forecast.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Genesis.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Governance.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Internal.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/IncrementalStake.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/NewEpochState.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/PulsingReward.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/Types.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/PParams.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/PoolRank.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/RewardProvenance.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/RewardUpdate.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rewards.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Bbody.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Deleg.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Delegs.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Delpl.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Epoch.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Ledger.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Ledgers.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Mir.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/NewEpoch.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Newpp.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Ppup.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Reports.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Rupd.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Snap.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Tick.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Upec.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxow.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/SoftForks.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/StabilityWindow.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/State.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/State/Account.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/State/CertState.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/State/Stake.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Transition.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Translation.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/Tx.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/TxAuxData.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/TxBody.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/TxCert.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/TxOut.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/TxWits.hs",
    "eras/shelley/impl/src/Cardano/Ledger/Shelley/UTxO.hs",
    "libs/cardano-data/src/Data/CanonicalMaps.hs",
    "libs/cardano-data/src/Data/ListMap.hs",
    "libs/cardano-data/src/Data/Map/NonEmpty.hs",
    "libs/cardano-data/src/Data/MapExtras.hs",
    "libs/cardano-data/src/Data/MonoTuple.hs",
    "libs/cardano-data/src/Data/OMap/Strict.hs",
    "libs/cardano-data/src/Data/OSet/Strict.hs",
    "libs/cardano-data/src/Data/Pulse.hs",
    "libs/cardano-data/src/Data/Set/NonEmpty.hs",
    "libs/cardano-data/src/Data/Universe.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Era.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Governance.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/PParams.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Scripts.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Scripts/Data.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Scripts/ExUnits.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/State/Query.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/State/Query/Account.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/State/Query/Governance.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Transition.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/Address.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/AuxData.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/Body.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/Cert.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/In.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/Out.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/Tx/Wits.hs",
    "libs/cardano-ledger-api/src/Cardano/Ledger/Api/UTxO.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Coders.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Crypto.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Annotated.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Coders.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/DecCBOR.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Decoder.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Drop.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Sharing.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Sized.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Encoding.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Encoding/Coders.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Encoding/EncCBOR.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Encoding/Encoder.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/FlatTerm.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Group.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Plain.hs",
    "libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Version.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/BasicTypes.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/LedgerCBOR.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/Blocks/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/EntitiesAccounts/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/EntitiesCommittee/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/EntitiesDReps/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/EntitiesStakePools/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/EntitiesStakePools/VRFKeyHashes/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/GovCommittee/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/GovConstitution/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/GovPParams/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/GovProposals/Roots/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/GovProposals/V0.hs",
    "libs/cardano-ledger-canonical-state/src/Cardano/Ledger/CanonicalState/Namespace/UTxO/V0.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Address.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/BHeaderView.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes/NonZero.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Block.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Coin.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Compactible.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core/Era.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core/PParams.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core/Translation.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core/TxCert.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Core/TxLevel.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Credential.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Genesis.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/HKD.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Hashes.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Keys.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Keys/Bootstrap.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Keys/Internal.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Keys/WitVKey.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/MemoBytes.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/MemoBytes/Internal.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Metadata.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Orphans.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/CostModels.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/Data.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/Evaluate.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/ExUnits.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/Language.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/ToPlutusData.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/TxInfo.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Rewards.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Rules/ValidationMode.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Slot.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/CertState.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/ChainAccount.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/Governance.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/PoolDistr.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/SnapShots.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/Stake.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/StakePool.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/State/UTxO.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Tools.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/TxIn.hs",
    "libs/cardano-ledger-core/src/Cardano/Ledger/Val.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/Crypto.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/API.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/BHeader.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/OCert.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/Rules/OCert.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/Rules/Overlay.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/Rules/Prtcl.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/Rules/Tickn.hs",
    "libs/cardano-protocol-tpraos/src/Cardano/Protocol/TPraos/Rules/Updn.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/Orphans.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/Query.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/Schema.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/Transform.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/UTxO.hs",
    "libs/ledger-state/src/Cardano/Ledger/State/Vector.hs",
    "libs/non-integral/src/Cardano/Ledger/NonIntegral.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Binary/V1.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Binary/V2.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Binary/V3.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Source/V1.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Source/V2.hs",
    "libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Source/V3.hs",
    "libs/small-steps/src/Control/Provenance.hs",
    "libs/small-steps/src/Control/State/Transition.hs",
    "libs/small-steps/src/Control/State/Transition/Extended.hs",
    "libs/small-steps/src/Control/State/Transition/Simple.hs",
    "libs/vector-map/src/Data/VMap.hs",
    "libs/vector-map/src/Data/VMap/KVVector.hs",
]

target_scopes = [
    "Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition",
    "Critical. Honest nodes accept an invalid block or transaction causing permanent ledger divergence requiring a hard fork",
    "Critical. Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted",
    "High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork",
    "High. Deterministic disagreement between honest nodes from ledger rule evaluation, era transition, serialization, or script/witness validation",
    "Medium. Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one Cardano Ledger target.

    ```
    target_file format:
    "'File Name: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs -> Scope: Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Cardano Ledger target:

    {target_file}

    Use live context from the project if available: ledger STS rules across Byron, Shelley, Allegra, Mary, Alonzo, Babbage, Conway, and Dijkstra; UTxO and UTxOW validation; certificates; withdrawals; rewards; deposits; protocol updates; governance; treasury; protocol parameters; block-body limits; scripts; witnesses; serialization; canonical state; and shared ledger libraries.

    Protocol focus:
    This repository contains the formal specifications, executable models, and Haskell implementations of the Cardano Ledger. The audit focus is whether attacker-controlled transactions, blocks, certificates, votes, proposals, witnesses, scripts, or serialized ledger data can make honest nodes accept invalid state, diverge, lose or freeze funds, misapply governance, or exceed bounty-scoped resource limits.

    Core invariants:

    * Invalid transactions, witnesses, scripts, certificates, proposals, votes, blocks, or serialized ledger states must not be accepted by honest nodes.
    * Valid transactions and blocks must remain processable without ledger divergence, unexpected rejection, chain halt, or hard-fork-only recovery.
    * UTxO, fee, deposit, refund, reward, treasury, withdrawal, donation, update, and governance accounting must preserve all ledger value under adversarial ordering.
    * Era-specific rules must enforce certificate authorization, protocol updates, hard-fork transitions, governance proposal/vote rules, ratification/enactment, and network/value constraints exactly as specified.
    * Native scripts, Plutus scripts, reference scripts, datums, redeemers, metadata, CBOR, hashes, and era translation paths must reject malformed or adversarial inputs safely.
    * Ledger validation must enforce intended limits for attacker-controlled transactions, blocks, certificates, votes, proposals, and scripts.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is unprivileged: transaction sender, block producer below consensus threshold, certificate/vote/proposal author, script author, witness provider, or serialized ledger/input producer.
    * Do not rely on governance majority, malicious supermajority, validator/operator compromise, leaked keys, third-party dependency compromise, Sybil/51% attacks, phishing, spam-only DoS, public-mainnet testing, or out-of-scope websites/dapps.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, state-transition, consensus, serialization, governance, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, model comparison, or differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Note any question u must target valid issue u think could be possible

    High-value attack surfaces:

    * Ledger orchestration: CHAIN, BBODY, LEDGERS, LEDGER, UTXO, UTXOW, CERTS, epoch, reward, pool, update, GOV, RATIFY, ENACT, and era-transition sequencing.
    * Funds and accounting: UTxO value conservation, fees, deposits, refunds, rewards, withdrawals, treasury donations, treasury withdrawals, MIR/update flows, and protocol-parameter updates.
    * Governance and updates: Byron/Shelley update rules plus Conway proposal validation, return accounts, guardrails policy, voter authorization, DRep/SPO/committee thresholds, previous-action chains, expiry, ratification, and enactment.
    * Scripts and witnesses: bootstrap witnesses, vkey witnesses, native scripts, Plutus scripts, redeemers, datums, reference scripts, script integrity hashes, and malformed witnesses.
    * Serialization and era state: CBOR decoders/encoders, canonical hashes, memo bytes, metadata, transaction bodies, certificates, governance actions, block bodies, and era translations.
    * Resource limits: max transaction/block sizes, execution units, collateral, reference script size per transaction/block, certificate/proposal/vote growth, and validation cost.

    Impact mapping:

    * Critical: Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.
    * Critical: Honest nodes accept an invalid block or transaction causing permanent ledger divergence requiring a hard fork.
    * Critical: Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.
    * High: Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.
    * High: Deterministic disagreement between honest nodes from ledger rule evaluation, era transition, serialization, or script/witness validation.
    * Medium: Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters.

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Cardano Ledger exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production Cardano Ledger code covered by the Intersect POSM Bug Bounty Program.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, scripts, configs, build files, IDE files, package metadata, vendored libraries, formal-spec documents, and local-only fixtures.

## Objective
Decide whether the question leads to a real, reachable Cardano Ledger vulnerability.
The attacker must be unprivileged and enter through transaction, block, certificate, vote, proposal, script, witness, CBOR/serialized input, or below-threshold protocol participation.
The impact must match one of the allowed Cardano Ledger impacts below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.
- Critical. Honest nodes accept an invalid block or transaction causing permanent ledger divergence requiring a hard fork.
- Critical. Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.
- High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.
- High. Deterministic disagreement between honest nodes from ledger rule evaluation, era transition, serialization, or script/witness validation.
- Medium. Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production Cardano Ledger files/functions.
3. Check the relevant guard: STS rule order, UTxO value conservation, witness/script checks, fee/deposit/refund/reward/treasury accounting, certificate/update/governance authorization, ratification/enactment thresholds where applicable, CBOR/canonical encoding bounds, or resource limits.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires governance majority, malicious supermajority, trusted role, leaked key, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or spam-only DoS.
- Only affects tests, docs, configs, scripts, mocks, local fixtures, formal specs, vendored libraries, or local deployment choices.
- External dependency behavior is the only cause.
- Impact is only logging, observability, local misconfiguration, non-security correctness, harmless reject, stale read, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Cardano Ledger.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production Cardano Ledger files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, formal-spec documents, local fixtures, vendored libraries, or package metadata as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on the Intersect POSM Bug Bounty scope for Cardano Ledger.
Focus on reachable issues triggered by an unprivileged transaction sender, block producer below consensus threshold, certificate/vote/proposal author, script author, witness provider, or serialized ledger/input producer.
Only report an analog if this codebase has its own reachable root cause and the impact matches one of the allowed Cardano Ledger impacts below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.
- Critical. Honest nodes accept an invalid block or transaction causing permanent ledger divergence requiring a hard fork.
- Critical. Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.
- High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.
- High. Deterministic disagreement between honest nodes from ledger rule evaluation, era transition, serialization, or script/witness validation.
- Medium. Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters.

## Method
1. Classify vuln type: invalid state transition, funds/accounting bug, certificate/update/governance bypass, ratification/enactment flaw, witness/script validation bypass, serialization/canonicalization bug, resource-limit bug, or ledger divergence.
2. Map to Cardano Ledger components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why this repository's code is a necessary vulnerable step.
6. Reject if the impact does not match one of the allowed Cardano Ledger impacts above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires governance majority, trusted role, leaked key, malicious supermajority, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or spam-only DoS.
- External dependency behavior is the only cause.
- Test/docs/config/build/formal-spec-only issue.
- Theoretical-only issue with no protocol impact.
- Impact is only local misconfiguration, observability noise, logging noise, harmless reject, stale read, or non-security correctness.
- Impact or likelihood missing.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict Cardano Ledger bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the Intersect POSM Bug Bounty Program for scope, exclusions, and valid severity.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject admin-only, governance-majority, consensus-threshold/validator-majority corruption, trusted-operator, leaked-key, host-compromise, best-practice, docs/style, config/build-only, fee-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, phishing/social engineering, DNS/BGP hijack, third-party exchange/dapp/oracle compromise, public-mainnet DoS testing, raw volumetric DDoS, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user or by a Byzantine protocol participant below the consensus fault threshold, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Reject any issue whose final impact is not one of the allowed Cardano Ledger impacts listed below.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
The claim must affect production in-scope Cardano Ledger code or systems, such as:
- Era ledger rules: CHAIN, BBODY, LEDGERS, LEDGER, UTXO, UTXOW, CERTS, epoch, reward, pool, update, GOV, RATIFY, ENACT, mempool, hard-fork, and era-transition validation.
- Transaction and state paths: UTxO spending, value conservation, witnesses, scripts, validity intervals, collateral, withdrawals, certificates, fees, deposits, refunds, rewards, donations, MIR/update flows, and treasury accounting.
- Governance and update paths: Byron/Shelley protocol updates, proposal creation, return accounts, guardrails policy, votes, DRep/SPO/committee authorization, thresholds, previous-action chains, expiry, ratification, enactment, protocol parameters, hard forks, constitution, and committee changes.
- Serialization and hashing: CBOR decoders/encoders, canonical state, memo bytes, transaction bodies, metadata, certificates, governance actions, script integrity hashes, block bodies, and era translation.
- Resource limits: transaction size, block size, block reference script size, per-transaction reference script size, execution units, collateral counts, proposal/vote/certificate growth, and validation cost.

Reject third-party dapps, unlisted public websites, tests, docs, examples, mocks, generated files, formal specifications, local deployment helpers, vendored libraries, and issues that only affect local developer tooling unless the submitted claim proves a direct in-scope Cardano Ledger security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.
- Critical. Honest nodes accept an invalid block or transaction causing permanent ledger divergence requiring a hard fork.
- Critical. Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.
- High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.
- High. Deterministic disagreement between honest nodes from ledger rule evaluation, era transition, serialization, or script/witness validation.
- Medium. Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters.

Informational, non-security correctness, observability/logging-only, harmless reject/revert, stale read without consensus/state/accounting/security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one of the allowed Cardano Ledger impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol/security/accounting/authorization/certification assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed Cardano Ledger impact above, with realistic likelihood.
6. Reproducible safe proof path: unit PoC, local private testnet, deterministic integration test, invariant/fuzz test, differential test, model comparison, or exact local manual steps.
7. No obvious rejection reason from SECURITY.md, Researcher.md if present, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or below-threshold Byzantine protocol participant trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by Cardano Ledger production protocol code, not by an external dependency alone?
- Is the ledger divergence/funds-loss/accounting/governance/resource impact concrete, not hypothetical?
- Does the claim avoid governance majority, validator-majority, trusted operator, leaked key, mainnet DoS, and third-party compromise assumptions?
- Would a bounty triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed Cardano Ledger bounty impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/model-comparison test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
