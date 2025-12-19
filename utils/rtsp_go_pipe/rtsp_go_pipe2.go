package main

import (
	"log"
	"net"
	"github.com/bluenviron/gortsplib/v4"
	"github.com/bluenviron/gortsplib/v4/pkg/base"
	"github.com/bluenviron/gortsplib/v4/pkg/description"
	"github.com/bluenviron/gortsplib/v4/pkg/format"
	"github.com/pion/rtp"
)

func main() {
	u, err := base.ParseURL("rtsp://mmtx01.apagaofogo.eco.br:8554/AoF-C0029-APASC")
	if err != nil {
		log.Fatal(err)
	}

	tcpTransport := gortsplib.TransportTCP
	client := gortsplib.Client{
		Transport: &tcpTransport,
	}
	defer client.Close()

	// Corrigido: separa host e porta
	host, port, _ := net.SplitHostPort(u.Host)
	err = client.Start(host, port)
	if err != nil {
		log.Fatal(err)
	}

	// Describe
	desc, _, err := client.Describe(u)
	if err != nil {
		log.Fatal(err)
	}

	// Setup
	err = client.SetupAll(desc.BaseURL, desc.Medias)
	if err != nil {
		log.Fatal(err)
	}

	// RTP callback
	client.OnPacketRTPAny(func(medi *description.Media, _ format.Format, pkt *rtp.Packet) {
		pts, ptsAvailable := client.PacketPTS2(medi, pkt)
		if ptsAvailable {
			frame := pkt.Payload
			log.Printf("Frame: tamanho=%d, timestamp=%.3f\n", len(frame), float64(pts)/90000.0)
			_ = frame // placeholder para uso posterior
		}
	})

	// Play
	_, err = client.Play(nil)
	if err != nil {
		log.Fatal(err)
	}

	log.Fatal(client.Wait())
}
