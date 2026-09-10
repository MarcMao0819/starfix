package fixture.sales.ledger;

import org.springframework.test.context.ActiveProfiles;

@ActiveProfiles(profiles = {"prod", "local", "candidate-readonly"})
class LedgerHarness {

    void inspectOnly() {
        jdbcTemplate.queryForList("SELECT ledger_no FROM t_ledger");
    }
}
