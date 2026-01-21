<?php
namespace App\Payment;

use App\Database\Repository;
use App\Email\Mailer;

class Payment {
    private $repository;
    private $mailer;

    public function __construct($repository, $mailer) {
        $this->repository = $repository;
        $this->mailer = $mailer;
    }

    public function process($amount) {
        $result = $this->repository->save(['amount' => $amount]);
        $this->mailer->send('Payment processed');
        return $result;
    }
}
