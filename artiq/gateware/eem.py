from migen import *
from migen.build.generic_platform import *
from migen.genlib.io import DifferentialOutput

from artiq.gateware import rtio
from artiq.gateware.rtio.phy import spi2, ad53xx_monitor, dds, grabber
from artiq.gateware.suservo import servo, pads as servo_pads
from artiq.gateware.rtio.phy import servo as rtservo, fastino, phaser


def _eem_signal(i):
    n = "d{}".format(i)
    if i == 0:
        n += "_cc"
    return n


def _eem_pin(eem, i, pol):
    return "eem{}:{}_{}".format(eem, _eem_signal(i), pol)


def default_iostandard(eem):
    return IOStandard("LVDS_25")


class _EEM:
    @classmethod
    def add_extension(cls, target, eem, *args, is_drtio_over_eem=False, **kwargs):
        name = cls.__name__
        target.platform.add_extension(cls.io(eem, *args, **kwargs))
        if is_drtio_over_eem:
            print("{} (EEM{}) starting at DRTIO channel 0x{:06x}"
                .format(name, eem, (len(target.gt_drtio.channels) + len(target.eem_drtio_channels) + 1) << 16))
        else:
            print("{} (EEM{}) starting at RTIO channel 0x{:06x}"
                .format(name, eem, len(target.rtio_channels)))


class DIO(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [("dio{}".format(eem), i,
            Subsignal("p", Pins(_eem_pin(eem, i, "p"))),
            Subsignal("n", Pins(_eem_pin(eem, i, "n"))),
            iostandard(eem))
            for i in range(8)]

    @classmethod
    def add_std(cls, target, eem, ttl03_cls, ttl47_cls, iostandard=default_iostandard,
            edge_counter_cls=None):
        cls.add_extension(target, eem, iostandard=iostandard)

        phys = []
        dci = iostandard(eem).name == "LVDS"
        for i in range(4):
            pads = target.platform.request("dio{}".format(eem), i)
            phy = ttl03_cls(pads.p, pads.n, dci=dci)
            phys.append(phy)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy))
        for i in range(4):
            pads = target.platform.request("dio{}".format(eem), 4+i)
            phy = ttl47_cls(pads.p, pads.n, dci=dci)
            phys.append(phy)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy))

        if edge_counter_cls is not None:
            for phy in phys:
                state = getattr(phy, "input_state", None)
                if state is not None:
                    counter = edge_counter_cls(state)
                    target.submodules += counter
                    target.rtio_channels.append(rtio.Channel.from_phy(counter))


class DIO_SPI(_EEM):
    @staticmethod
    def io(eem, spi, ttl, iostandard):
        def spi_subsignals(clk, mosi, miso, cs, pol):
            signals = [Subsignal("clk", Pins(_eem_pin(eem, clk, pol)))]
            if mosi is not None:
                signals.append(Subsignal("mosi",
                                         Pins(_eem_pin(eem, mosi, pol))))
            if miso is not None:
                signals.append(Subsignal("miso",
                                         Pins(_eem_pin(eem, miso, pol))))
            if cs:
                signals.append(Subsignal("cs_n", Pins(
                    *(_eem_pin(eem, pin, pol) for pin in cs))))
            return signals

        spi = [
            ("dio{}_spi{}_{}".format(eem, i, pol), i,
             *spi_subsignals(clk, mosi, miso, cs, pol),
             iostandard(eem))
            for i, (clk, mosi, miso, cs) in enumerate(spi) for pol in "pn"
        ]
        ttl = [
            ("dio{}".format(eem), i,
             Subsignal("p", Pins(_eem_pin(eem, pin, "p"))),
             Subsignal("n", Pins(_eem_pin(eem, pin, "n"))),
             iostandard(eem))
            for i, (pin, _, _) in enumerate(ttl)
        ]
        return spi + ttl

    @classmethod
    def add_std(cls, target, eem, spi, ttl, iostandard=default_iostandard):
        cls.add_extension(target, eem, spi, ttl, iostandard=iostandard)

        for i in range(len(spi)):
            phy = spi2.SPIMaster(
                target.platform.request("dio{}_spi{}_p".format(eem, i)),
                target.platform.request("dio{}_spi{}_n".format(eem, i))
            )
            target.submodules += phy
            target.rtio_channels.append(
                rtio.Channel.from_phy(phy, ififo_depth=4))

        dci = iostandard(eem).name == "LVDS"
        for i, (_, ttl_cls, edge_counter_cls) in enumerate(ttl):
            pads = target.platform.request("dio{}".format(eem), i)
            phy = ttl_cls(pads.p, pads.n, dci=dci)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy))

            if edge_counter_cls is not None:
                state = getattr(phy, "input_state", None)
                if state is not None:
                    counter = edge_counter_cls(state)
                    target.submodules += counter
                    target.rtio_channels.append(rtio.Channel.from_phy(counter))


class Urukul(_EEM):
    @staticmethod
    def io(eem, eem_aux, iostandard):
        ios = [
            ("urukul{}_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "p"))),
                Subsignal("cs_n", Pins(
                    *(_eem_pin(eem, i + 3, "p") for i in range(3)))),
                iostandard(eem),
            ),
            ("urukul{}_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "n"))),
                Subsignal("cs_n", Pins(
                    *(_eem_pin(eem, i + 3, "n") for i in range(3)))),
                iostandard(eem),
            ),
        ]
        ttls = [(6, eem, "io_update"),
                (7, eem, "dds_reset_sync_in", Misc("IOB=TRUE"))]
        if eem_aux is not None:
            ttls += [(0, eem_aux, "sync_clk"),
                     (1, eem_aux, "sync_in"),
                     (2, eem_aux, "io_update_ret"),
                     (3, eem_aux, "nu_mosi3"),
                     (4, eem_aux, "sw0"),
                     (5, eem_aux, "sw1"),
                     (6, eem_aux, "sw2"),
                     (7, eem_aux, "sw3")]
        for i, j, sig, *extra_args in ttls:
            ios.append(
                ("urukul{}_{}".format(eem, sig), 0,
                    Subsignal("p", Pins(_eem_pin(j, i, "p"))),
                    Subsignal("n", Pins(_eem_pin(j, i, "n"))),
                    iostandard(j), *extra_args
                ))
        return ios

    @staticmethod
    def io_qspi(eem0, eem1, iostandard):
        ios = [
            ("urukul{}_spi_p".format(eem0), 0,
                Subsignal("clk", Pins(_eem_pin(eem0, 0, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem0, 1, "p"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem0, 3, "p"), _eem_pin(eem0, 4, "p"))),
                iostandard(eem0),
            ),
            ("urukul{}_spi_n".format(eem0), 0,
                Subsignal("clk", Pins(_eem_pin(eem0, 0, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem0, 1, "n"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem0, 3, "n"), _eem_pin(eem0, 4, "n"))),
                iostandard(eem0),
            ),
        ]
        ttls = [(6, eem0, "io_update"),
                (7, eem0, "dds_reset_sync_in"),
                (4, eem1, "sw0"),
                (5, eem1, "sw1"),
                (6, eem1, "sw2"),
                (7, eem1, "sw3")]
        for i, j, sig in ttls:
            ios.append(
                ("urukul{}_{}".format(eem0, sig), 0,
                    Subsignal("p", Pins(_eem_pin(j, i, "p"))),
                    Subsignal("n", Pins(_eem_pin(j, i, "n"))),
                    iostandard(j)
                ))
        ios += [
            ("urukul{}_qspi_p".format(eem0), 0,
                Subsignal("cs", Pins(_eem_pin(eem0, 5, "p")), iostandard(eem0)),
                Subsignal("clk", Pins(_eem_pin(eem0, 2, "p")), iostandard(eem0)),
                Subsignal("mosi0", Pins(_eem_pin(eem1, 0, "p")), iostandard(eem1)),
                Subsignal("mosi1", Pins(_eem_pin(eem1, 1, "p")), iostandard(eem1)),
                Subsignal("mosi2", Pins(_eem_pin(eem1, 2, "p")), iostandard(eem1)),
                Subsignal("mosi3", Pins(_eem_pin(eem1, 3, "p")), iostandard(eem1)),
            ),
            ("urukul{}_qspi_n".format(eem0), 0,
                Subsignal("cs", Pins(_eem_pin(eem0, 5, "n")), iostandard(eem0)),
                Subsignal("clk", Pins(_eem_pin(eem0, 2, "n")), iostandard(eem0)),
                Subsignal("mosi0", Pins(_eem_pin(eem1, 0, "n")), iostandard(eem1)),
                Subsignal("mosi1", Pins(_eem_pin(eem1, 1, "n")), iostandard(eem1)),
                Subsignal("mosi2", Pins(_eem_pin(eem1, 2, "n")), iostandard(eem1)),
                Subsignal("mosi3", Pins(_eem_pin(eem1, 3, "n")), iostandard(eem1)),
            ),
        ]
        return ios

    @classmethod
    def add_std(cls, target, eem, eem_aux, ttl_out_cls, dds_type, proto_rev,
                sync_gen_cls=None, iostandard=default_iostandard):
        cls.add_extension(target, eem, eem_aux, iostandard=iostandard)

        spi_phy = spi2.SPIMaster(target.platform.request("urukul{}_spi_p".format(eem)),
            target.platform.request("urukul{}_spi_n".format(eem)))
        target.submodules += spi_phy
        target.rtio_channels.append(rtio.Channel.from_phy(spi_phy, ififo_depth=4))

        pads = target.platform.request("urukul{}_dds_reset_sync_in".format(eem))
        if dds_type == "ad9912":
            # DDS_RESET for AD9912 variant only
            target.specials += DifferentialOutput(0, pads.p, pads.n)
        elif sync_gen_cls is not None:  # AD9910 variant and SYNC_IN from EEM
            sync_phy = sync_gen_cls(pad=pads.p, pad_n=pads.n, ftw_width=4)
            target.submodules += sync_phy
            target.rtio_channels.append(rtio.Channel.from_phy(sync_phy))

        pads = target.platform.request("urukul{}_io_update".format(eem))
        io_upd_phy = ttl_out_cls(pads.p, pads.n)
        target.submodules += io_upd_phy
        target.rtio_channels.append(rtio.Channel.from_phy(io_upd_phy))

        dds_monitor = dds.UrukulMonitor(spi_phy, io_upd_phy, dds_type, proto_rev)
        target.submodules += dds_monitor
        spi_phy.probes.extend(dds_monitor.probes)

        if eem_aux is not None:
            for signal in "sw0 sw1 sw2 sw3".split():
                pads = target.platform.request("urukul{}_{}".format(eem, signal))
                phy = ttl_out_cls(pads.p, pads.n)
                target.submodules += phy
                target.rtio_channels.append(rtio.Channel.from_phy(phy))


class Sampler(_EEM):
    @staticmethod
    def io(eem, eem_aux, iostandard):
        ios = [
            ("sampler{}_adc_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 1, "p")), Misc("DIFF_TERM=TRUE")),
                iostandard(eem),
            ),
            ("sampler{}_adc_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 1, "n")), Misc("DIFF_TERM=TRUE")),
                iostandard(eem),
            ),
            ("sampler{}_pgia_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 4, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 5, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 6, "p")), Misc("DIFF_TERM=TRUE")),
                Subsignal("cs_n", Pins(_eem_pin(eem, 7, "p"))),
                iostandard(eem),
            ),
            ("sampler{}_pgia_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 4, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 5, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 6, "n")), Misc("DIFF_TERM=TRUE")),
                Subsignal("cs_n", Pins(_eem_pin(eem, 7, "n"))),
                iostandard(eem),
            ),
        ] + [
            ("sampler{}_{}".format(eem, sig), 0,
                Subsignal("p", Pins(_eem_pin(j, i, "p"))),
                Subsignal("n", Pins(_eem_pin(j, i, "n"))),
                iostandard(j)
            ) for i, j, sig in [
                (2, eem, "sdr"),
                (3, eem, "cnv")
            ]
        ]
        if eem_aux is not None:
            ios += [
                ("sampler{}_adc_data_p".format(eem), 0,
                    Subsignal("clkout", Pins(_eem_pin(eem_aux, 0, "p"))),
                    Subsignal("sdoa", Pins(_eem_pin(eem_aux, 1, "p"))),
                    Subsignal("sdob", Pins(_eem_pin(eem_aux, 2, "p"))),
                    Subsignal("sdoc", Pins(_eem_pin(eem_aux, 3, "p"))),
                    Subsignal("sdod", Pins(_eem_pin(eem_aux, 4, "p"))),
                    Misc("DIFF_TERM=TRUE"),
                    iostandard(eem_aux),
                ),
                ("sampler{}_adc_data_n".format(eem), 0,
                    Subsignal("clkout", Pins(_eem_pin(eem_aux, 0, "n"))),
                    Subsignal("sdoa", Pins(_eem_pin(eem_aux, 1, "n"))),
                    Subsignal("sdob", Pins(_eem_pin(eem_aux, 2, "n"))),
                    Subsignal("sdoc", Pins(_eem_pin(eem_aux, 3, "n"))),
                    Subsignal("sdod", Pins(_eem_pin(eem_aux, 4, "n"))),
                    Misc("DIFF_TERM=TRUE"),
                    iostandard(eem_aux),
                ),
            ]
        return ios

    @classmethod
    def add_std(cls, target, eem, eem_aux, ttl_out_cls, iostandard=default_iostandard):
        cls.add_extension(target, eem, eem_aux, iostandard=iostandard)

        phy = spi2.SPIMaster(
                target.platform.request("sampler{}_adc_spi_p".format(eem)),
                target.platform.request("sampler{}_adc_spi_n".format(eem)))
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))
        phy = spi2.SPIMaster(
                target.platform.request("sampler{}_pgia_spi_p".format(eem)),
                target.platform.request("sampler{}_pgia_spi_n".format(eem)))
        target.submodules += phy

        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))
        pads = target.platform.request("sampler{}_cnv".format(eem))
        phy = ttl_out_cls(pads.p, pads.n)
        target.submodules += phy

        target.rtio_channels.append(rtio.Channel.from_phy(phy))
        sdr = target.platform.request("sampler{}_sdr".format(eem))
        target.specials += DifferentialOutput(1, sdr.p, sdr.n)


class Novogorny(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [
            ("novogorny{}_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "p"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem, 3, "p"), _eem_pin(eem, 4, "p"))),
                iostandard(eem),
            ),
            ("novogorny{}_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "n"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem, 3, "n"), _eem_pin(eem, 4, "n"))),
                iostandard(eem),
            ),
        ] + [
            ("novogorny{}_{}".format(eem, sig), 0,
                Subsignal("p", Pins(_eem_pin(j, i, "p"))),
                Subsignal("n", Pins(_eem_pin(j, i, "n"))),
                iostandard(j)
            ) for i, j, sig in [
                (5, eem, "cnv"),
                (6, eem, "busy"),
                (7, eem, "scko"),
            ]
        ]

    @classmethod
    def add_std(cls, target, eem, ttl_out_cls, iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        phy = spi2.SPIMaster(target.platform.request("novogorny{}_spi_p".format(eem)),
                target.platform.request("novogorny{}_spi_n".format(eem)))
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=16))

        pads = target.platform.request("novogorny{}_cnv".format(eem))
        phy = ttl_out_cls(pads.p, pads.n)
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy))


class Zotino(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [
            ("zotino{}_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "p"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem, 3, "p"), _eem_pin(eem, 4, "p"))),
                iostandard(eem),
            ),
            ("zotino{}_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "n"))),
                Subsignal("cs_n", Pins(
                    _eem_pin(eem, 3, "n"), _eem_pin(eem, 4, "n"))),
                iostandard(eem),
            ),
        ] + [
            ("zotino{}_{}".format(eem, sig), 0,
                    Subsignal("p", Pins(_eem_pin(j, i, "p"))),
                    Subsignal("n", Pins(_eem_pin(j, i, "n"))),
                    iostandard(j)
            ) for i, j, sig in [
                (5, eem, "ldac_n"),
                (6, eem, "busy"),
                (7, eem, "clr_n"),
            ]
        ]

    @classmethod
    def add_std(cls, target, eem, ttl_out_cls, iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        spi_phy = spi2.SPIMaster(target.platform.request("zotino{}_spi_p".format(eem)),
            target.platform.request("zotino{}_spi_n".format(eem)))
        target.submodules += spi_phy
        target.rtio_channels.append(rtio.Channel.from_phy(spi_phy, ififo_depth=4))

        pads = target.platform.request("zotino{}_ldac_n".format(eem))
        ldac_phy = ttl_out_cls(pads.p, pads.n)
        target.submodules += ldac_phy
        target.rtio_channels.append(rtio.Channel.from_phy(ldac_phy))

        pads = target.platform.request("zotino{}_clr_n".format(eem))
        clr_phy = ttl_out_cls(pads.p, pads.n)
        target.submodules += clr_phy
        target.rtio_channels.append(rtio.Channel.from_phy(clr_phy))

        dac_monitor = ad53xx_monitor.AD53XXMonitor(spi_phy.rtlink, ldac_phy.rtlink)
        target.submodules += dac_monitor
        spi_phy.probes.extend(dac_monitor.probes)


class Grabber(_EEM):
    @staticmethod
    def io(eem, eem_aux, iostandard):
        ios = [
            ("grabber{}_video".format(eem), 0,
                Subsignal("clk_p", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("clk_n", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("sdi_p", Pins(*[_eem_pin(eem, i, "p") for i in range(1, 5)])),
                Subsignal("sdi_n", Pins(*[_eem_pin(eem, i, "n") for i in range(1, 5)])),
                iostandard(eem), Misc("DIFF_TERM=TRUE")
            ),
            ("grabber{}_cc0".format(eem), 0,
                Subsignal("p", Pins(_eem_pin(eem, 5, "p"))),
                Subsignal("n", Pins(_eem_pin(eem, 5, "n"))),
                iostandard(eem)
            ),
            ("grabber{}_cc1".format(eem), 0,
                Subsignal("p", Pins(_eem_pin(eem, 6, "p"))),
                Subsignal("n", Pins(_eem_pin(eem, 6, "n"))),
                iostandard(eem)
            ),
            ("grabber{}_cc2".format(eem), 0,
                Subsignal("p", Pins(_eem_pin(eem, 7, "p"))),
                Subsignal("n", Pins(_eem_pin(eem, 7, "n"))),
                iostandard(eem)
            ),
        ]
        if eem_aux is not None:
            ios += [
                ("grabber{}_video_m".format(eem), 0,
                    Subsignal("clk_p", Pins(_eem_pin(eem_aux, 0, "p"))),
                    Subsignal("clk_n", Pins(_eem_pin(eem_aux, 0, "n"))),
                    Subsignal("sdi_p", Pins(*[_eem_pin(eem_aux, i, "p") for i in range(1, 5)])),
                    Subsignal("sdi_n", Pins(*[_eem_pin(eem_aux, i, "n") for i in range(1, 5)])),
                    iostandard(eem_aux), Misc("DIFF_TERM=TRUE")
                ),
                ("grabber{}_serrx".format(eem), 0,
                    Subsignal("p", Pins(_eem_pin(eem_aux, 5, "p"))),
                    Subsignal("n", Pins(_eem_pin(eem_aux, 5, "n"))),
                    iostandard(eem_aux), Misc("DIFF_TERM=TRUE")
                ),
                ("grabber{}_sertx".format(eem), 0,
                    Subsignal("p", Pins(_eem_pin(eem_aux, 6, "p"))),
                    Subsignal("n", Pins(_eem_pin(eem_aux, 6, "n"))),
                    iostandard(eem_aux)
                ),
                ("grabber{}_cc3".format(eem), 0,
                    Subsignal("p", Pins(_eem_pin(eem_aux, 7, "p"))),
                    Subsignal("n", Pins(_eem_pin(eem_aux, 7, "n"))),
                    iostandard(eem_aux)
                ),
            ]
        return ios

    @classmethod
    def add_std(cls, target, eem, eem_aux, eem_aux2, ttl_out_cls, roi_engine_count,
            iostandard=default_iostandard):
        cls.add_extension(target, eem, eem_aux, iostandard=iostandard)

        pads = target.platform.request("grabber{}_video".format(eem))
        target.platform.add_period_constraint(pads.clk_p, 14.71)
        phy = grabber.Grabber(pads, roi_engine_count=roi_engine_count)
        name = "grabber{}".format(len(target.grabber_csr_group))
        setattr(target.submodules, name, phy)

        target.platform.add_false_path_constraints(
            target.crg.cd_sys.clk, phy.deserializer.cd_cl.clk)
        # Avoid bogus s/h violations at the clock input being sampled
        # by the ISERDES. This uses dynamic calibration.
        target.platform.add_false_path_constraints(
            pads.clk_p, phy.deserializer.cd_cl7x.clk)

        target.grabber_csr_group.append(name)
        target.csr_devices.append(name)
        target.rtio_channels += [
            rtio.Channel(phy.config),
            rtio.Channel(phy.gate_data)
        ]

        if ttl_out_cls is not None:
            for signal in "cc0 cc1 cc2".split():
                pads = target.platform.request("grabber{}_{}".format(eem, signal))
                phy = ttl_out_cls(pads.p, pads.n)
                target.submodules += phy
                target.rtio_channels.append(rtio.Channel.from_phy(phy))
            if eem_aux is not None:
                pads = target.platform.request("grabber{}_cc3".format(eem))
                phy = ttl_out_cls(pads.p, pads.n)
                target.submodules += phy
                target.rtio_channels.append(rtio.Channel.from_phy(phy))


class SUServo(_EEM):
    @staticmethod
    def io(*eems, iostandard):
        assert len(eems) in (4, 6)
        io = (Sampler.io(*eems[0:2], iostandard=iostandard)
                + Urukul.io_qspi(*eems[2:4], iostandard=iostandard))
        if len(eems) == 6:  # two Urukuls
            io += Urukul.io_qspi(*eems[4:6], iostandard=iostandard)
        return io

    @classmethod
    def add_std(cls, target, eems_sampler, eems_urukul,
                t_rtt=4, clk=1, shift=11, profile=5,
                iostandard=default_iostandard):
        """Add a 8-channel Sampler-Urukul Servo

        :param t_rtt: upper estimate for clock round-trip propagation time from
            ``sck`` at the FPGA to ``clkout`` at the FPGA, measured in RTIO
            coarse cycles (default: 4). This is the sum of the round-trip
            cabling delay and the 8 ns max propagation delay on Sampler (ADC
            and LVDS drivers). Increasing ``t_rtt`` increases servo latency.
            With all other parameters at their default values, ``t_rtt`` values
            above 4 also increase the servo period (reduce servo bandwidth).
        :param clk: DDS SPI clock cycle half-width in RTIO coarse cycles
            (default: 1)
        :param shift: fixed-point scaling factor for IIR coefficients
            (default: 11)
        :param profile: log2 of the number of profiles for each DDS channel
            (default: 5)
        """
        cls.add_extension(
            target, *(eems_sampler + sum(eems_urukul, [])),
            iostandard=iostandard)
        eem_sampler = "sampler{}".format(eems_sampler[0])
        eem_urukul = ["urukul{}".format(i[0]) for i in eems_urukul]

        sampler_pads = servo_pads.SamplerPads(target.platform, eem_sampler)
        urukul_pads = servo_pads.UrukulPads(
            target.platform, *eem_urukul)
        target.submodules += sampler_pads, urukul_pads
        # timings in units of RTIO coarse period
        adc_p = servo.ADCParams(width=16, channels=8, lanes=4, t_cnvh=4,
                                # account for SCK DDR to CONV latency
                                # difference (4 cycles measured)
                                t_conv=57 - 4, t_rtt=t_rtt + 4)
        iir_p = servo.IIRWidths(state=25, coeff=18, adc=16, asf=14, word=16,
                                accu=48, shift=shift, channel=3,
                                profile=profile, dly=8)
        dds_p = servo.DDSParams(width=8 + 32 + 16 + 16,
                                channels=adc_p.channels, clk=clk)
        su = servo.Servo(sampler_pads, urukul_pads, adc_p, iir_p, dds_p)
        su = ClockDomainsRenamer("rio_phy")(su)
        # explicitly name the servo submodule to enable the migen namer to derive
        # a name for the adc return clock domain
        setattr(target.submodules, "suservo_eem{}".format(eems_sampler[0]), su)

        ctrls = [rtservo.RTServoCtrl(ctrl) for ctrl in su.iir.ctrl]
        target.submodules += ctrls
        target.rtio_channels.extend(
            rtio.Channel.from_phy(ctrl) for ctrl in ctrls)
        mem = rtservo.RTServoMem(iir_p, su)
        target.submodules += mem
        target.rtio_channels.append(rtio.Channel.from_phy(mem, ififo_depth=4))

        phy = spi2.SPIMaster(
            target.platform.request("{}_pgia_spi_p".format(eem_sampler)),
            target.platform.request("{}_pgia_spi_n".format(eem_sampler)))
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))

        for i in range(2):
            if len(eem_urukul) > i:
                spi_p, spi_n = (
                    target.platform.request("{}_spi_p".format(eem_urukul[i])),
                    target.platform.request("{}_spi_n".format(eem_urukul[i])))
            else:  # create a dummy bus
                spi_p = Record([("clk", 1), ("cs_n", 1)])  # mosi, cs_n
                spi_n = None

            phy = spi2.SPIMaster(spi_p, spi_n)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))

        for j, eem_urukuli in enumerate(eem_urukul):
            pads = target.platform.request("{}_dds_reset_sync_in".format(eem_urukuli))
            target.specials += DifferentialOutput(0, pads.p, pads.n)

            for i, signal in enumerate("sw0 sw1 sw2 sw3".split()):
                pads = target.platform.request("{}_{}".format(eem_urukuli, signal))
                target.specials += DifferentialOutput(
                    su.iir.ctrl[j*4 + i].en_out, pads.p, pads.n)


class Mirny(_EEM):
    @staticmethod
    def io(eem, iostandard):
        ios = [
            ("mirny{}_spi_p".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "p"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "p"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "p")), Misc("DIFF_TERM=TRUE")),
                Subsignal("cs_n", Pins(_eem_pin(eem, 3, "p"))),
                iostandard(eem),
            ),
            ("mirny{}_spi_n".format(eem), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, "n"))),
                Subsignal("mosi", Pins(_eem_pin(eem, 1, "n"))),
                Subsignal("miso", Pins(_eem_pin(eem, 2, "n")), Misc("DIFF_TERM=TRUE")),
                Subsignal("cs_n", Pins(_eem_pin(eem, 3, "n"))),
                iostandard(eem),
            ),
        ]
        for i in range(4):
            ios.append(
                ("mirny{}_io{}".format(eem, i), 0,
                    Subsignal("p", Pins(_eem_pin(eem, 4 + i, "p"))),
                    Subsignal("n", Pins(_eem_pin(eem, 4 + i, "n"))),
                    iostandard(eem)
                ))
        return ios

    @classmethod
    def add_std(cls, target, eem, ttl_out_cls, iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        phy = spi2.SPIMaster(
            target.platform.request("mirny{}_spi_p".format(eem)),
            target.platform.request("mirny{}_spi_n".format(eem)))
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))

        for i in range(4):
            pads = target.platform.request("mirny{}_io{}".format(eem, i))
            phy = ttl_out_cls(pads.p, pads.n)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy))


class Fastino(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [
            ("fastino{}_ser_{}".format(eem, pol), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, pol))),
                Subsignal("mosi", Pins(*(_eem_pin(eem, i, pol)
                    for i in range(1, 7)))),
                Subsignal("miso", Pins(_eem_pin(eem, 7, pol)),
                          Misc("DIFF_TERM=TRUE")),
                iostandard(eem),
            ) for pol in "pn"]

    @classmethod
    def add_std(cls, target, eem, log2_width, iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        phy = fastino.Fastino(target.platform.request("fastino{}_ser_p".format(eem)),
            target.platform.request("fastino{}_ser_n".format(eem)),
            log2_width=log2_width)
        target.submodules += phy
        target.rtio_channels.append(rtio.Channel.from_phy(phy, ififo_depth=4))


class Phaser(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [
            ("phaser{}_ser_{}".format(eem, pol), 0,
                Subsignal("clk", Pins(_eem_pin(eem, 0, pol))),
                Subsignal("mosi", Pins(*(_eem_pin(eem, i, pol)
                    for i in range(1, 7)))),
                Subsignal("miso", Pins(_eem_pin(eem, 7, pol)),
                          Misc("DIFF_TERM=TRUE")),
                iostandard(eem),
            ) for pol in "pn"]

    @classmethod
    def add_std(cls, target, eem, mode="base", iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        if mode == "base":
            phy = phaser.Base(
                target.platform.request("phaser{}_ser_p".format(eem)),
                target.platform.request("phaser{}_ser_n".format(eem)))
            target.submodules += phy
            target.rtio_channels.extend([
                rtio.Channel.from_phy(phy, ififo_depth=4),
                rtio.Channel.from_phy(phy.ch0.frequency),
                rtio.Channel.from_phy(phy.ch0.phase_amplitude),
                rtio.Channel.from_phy(phy.ch1.frequency),
                rtio.Channel.from_phy(phy.ch1.phase_amplitude),
            ])
        elif mode == "miqro":
            phy = phaser.Miqro(
                target.platform.request("phaser{}_ser_p".format(eem)),
                target.platform.request("phaser{}_ser_n".format(eem)))
            target.submodules += phy
            target.rtio_channels.extend([
                rtio.Channel.from_phy(phy, ififo_depth=4),
                rtio.Channel.from_phy(phy.ch0),
                rtio.Channel.from_phy(phy.ch1),
            ])
        else:
            raise ValueError("invalid mode", mode)


class HVAmp(_EEM):
    @staticmethod
    def io(eem, iostandard):
        return [
            ("hvamp{}_out_en".format(eem), i,
                    Subsignal("p", Pins(_eem_pin(eem, i, "p"))),
                    Subsignal("n", Pins(_eem_pin(eem, i, "n"))),
                    iostandard(eem)
            ) for i in range(8)]

    @classmethod
    def add_std(cls, target, eem, ttl_out_cls, iostandard=default_iostandard):
        cls.add_extension(target, eem, iostandard=iostandard)

        for i in range(8):
            pads = target.platform.request("hvamp{}_out_en".format(eem), i)
            phy = ttl_out_cls(pads.p, pads.n)
            target.submodules += phy
            target.rtio_channels.append(rtio.Channel.from_phy(phy))


class Shuttler(_EEM):
    @staticmethod
    def io(eem, iostandard=default_iostandard):
        # Master: Pair 0~3 data IN, 4~7 OUT
        data_in = ("shuttler{}_drtio_rx".format(eem), 0,
            Subsignal("p", Pins("{} {} {} {}".format(*[
                _eem_pin(eem, i, "p") for i in range(4)
            ]))),
            Subsignal("n", Pins("{} {} {} {}".format(*[
                _eem_pin(eem, i, "n") for i in range(4)
            ]))),
            iostandard(eem),
            Misc("DIFF_TERM=TRUE"),
        )

        data_out = ("shuttler{}_drtio_tx".format(eem), 0,
            Subsignal("p", Pins("{} {} {} {}".format(*[
                _eem_pin(eem, i, "p") for i in range(4, 8)
            ]))),
            Subsignal("n", Pins("{} {} {} {}".format(*[
                _eem_pin(eem, i, "n") for i in range(4, 8)
            ]))),
            iostandard(eem),
        )

        return [data_in, data_out]

    @classmethod
    def add_std(cls, target, eem, eem_aux, iostandard=default_iostandard):
        cls.add_extension(target, eem, is_drtio_over_eem=True, iostandard=iostandard)
        target.eem_drtio_channels.append((target.platform.request("shuttler{}_drtio_rx".format(eem), 0), target.platform.request("shuttler{}_drtio_tx".format(eem), 0)))
